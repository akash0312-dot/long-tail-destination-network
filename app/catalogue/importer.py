"""Catalogue import job: a CSV of destinations in, catalogue rows + a row-by-row report out.

Per row: validate -> infer connector from the URL when missing -> de-duplicate (normalised URL + platform, within the
file and against the catalogue) -> detect language (flag mismatches) -> reachability (concurrent HEAD/GET) ->
enrichment (member counts from Lemmy / Discourse) -> relevance 1-5 with a reason -> insert or update.
Nothing is posted anywhere: an imported destination is catalogued (L0) until it is certified.
"""
import asyncio
import csv
import io
import json
import logging
import re
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from app.ai import get_provider
from app.catalogue.language import detect
from app.config import get_settings
from app.connectors.registry import all_connectors
from app.db import session_scope
from app.errors import AppError, ErrorCode
from app.jobs import JobContext, handler
from app.models import Destination
from app.output_manifest import build_manifest
from app.rules import primary_lang
from app.storage import get_storage, put_json
from app.urls import normalize_url, url_refused


def guard_url(url: str) -> None:
    host = urlsplit(url).hostname
    if urlsplit(url).scheme not in ("http", "https") or url_refused(host, get_settings().allow_internal_hosts):
        raise AppError(ErrorCode.CATALOGUE_INPUT_INVALID, "URL must be http(s) on a public host", {"host": host})

log = logging.getLogger("app.catalogue")
MAX_ROWS = 20_000
FIELDS = ["name", "url", "platform_key", "surface", "category", "audience_tags", "language", "geo", "est_audience",
          "rules_url", "frequency_cap", "self_promo_allowed", "link_policy", "disclosure_required",
          "automation_allowed", "connector_key", "relevance_score", "relevance_reason", "level", "last_certified_at",
          "description"]
MAJOR = {"youtube.com", "instagram.com", "facebook.com", "linkedin.com", "tiktok.com", "x.com", "twitter.com",
         "threads.net", "pinterest.com", "bsky.app", "reddit.com", "vimeo.com", "wordpress.com", "t.me",
         "telegram.org", "discord.com", "discord.gg"}
INFER = [(r"(^|\.)dev\.to$", "devto"), (r"(^|\.)hashnode\.(dev|com|net)$", "hashnode"),
         (r"(^|\.)ghost\.io$", "ghost")]
TRUE, FALSE = {"true", "yes", "y", "1"}, {"false", "no", "n", "0"}
REPORT_COLUMNS = ["row", "status", "destination_id", "name", "url", "normalized_url", "connector_key", "language",
                  "reachable", "http_status", "est_audience", "relevance_score", "errors", "warnings"]


def _bool(v: str, field: str, errors: list) -> bool | None:
    v = (v or "").strip().lower()
    if not v:
        return None
    if v in TRUE or v in FALSE:
        return v in TRUE
    errors.append(f"{field}: '{v}' is not true/false")
    return None


def _cap(v: str, errors: list) -> dict | None:
    """'1/7d', '3/1d', '2/12h' or JSON {"posts": 1, "per_days": 7}."""
    v = (v or "").strip()
    if not v:
        return None
    if v.startswith("{"):
        try:
            cap = json.loads(v)
            if isinstance(cap.get("posts"), int):
                return cap
        except (ValueError, AttributeError):
            pass
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)\s*([dh])", v.lower())
    if m:
        return {"posts": int(m.group(1)), "per_days" if m.group(3) == "d" else "per_hours": int(m.group(2))}
    errors.append(f"frequency_cap: '{v}' is not like 1/7d or JSON")
    return None


def _links(v: str, errors: list) -> dict | None:
    v = (v or "").strip().lower()
    if not v:
        return None
    if v in ("none", "no", "banned"):
        return {"allowed": False}
    if v in ("any", "yes", "allowed"):
        return {"allowed": True}
    if v.startswith("max:") and v[4:].isdigit():
        return {"allowed": True, "max_links": int(v[4:])}
    try:
        return json.loads(v)
    except ValueError:
        errors.append(f"link_policy: '{v}' is not none/any/max:N or JSON")
        return None


def infer_connector(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    for pattern, key in INFER:
        if re.search(pattern, host):
            return key
    if "/c/" in urlsplit(url).path and re.search(r"lemmy|lemm\.ee|sh\.itjust|feddit|programming\.dev", host):
        return "lemmy"
    return None


def derive_params(connector: str, url: str) -> dict:
    """Instance URL = everything before /c/<name> (Lemmy community, Discourse category), else the URL itself."""
    p = urlsplit(url)
    path = p.path.rstrip("/")
    m = re.search(r"/c/([^/?#]+)", path)
    base = f"{p.scheme}://{p.netloc}{path[: m.start()] if m else path}"
    if connector == "lemmy":
        return {"base_url": base, **({"community": m.group(1)} if m else {})}
    if connector in ("discourse", "ghost", "peertube", "devto"):
        return {"base_url": base}
    return {}


def validate_row(n: int, raw: dict, known: set[str]) -> tuple[dict | None, list[str], list[str]]:
    errors, warnings = [], []
    row = {k: (raw.get(k) or "").strip() for k in FIELDS}
    if not row["name"]:
        errors.append("name: required")
    url = row["url"]
    p = urlsplit(url if "://" in url else f"https://{url}")
    if not url or " " in url or p.scheme not in ("http", "https") or not p.hostname:
        errors.append(f"url: '{url}' is not a valid http(s) URL")
        return None, errors, warnings
    if url_refused(p.hostname, get_settings().allow_internal_hosts):
        errors.append(f"url: '{p.hostname}' is a private or internal host")
        return None, errors, warnings
    url = p.geturl()
    host = p.hostname.lower().removeprefix("www.")
    if host in MAJOR:
        errors.append(f"url: {host} is one of the 15 major platforms (already connected; out of scope)")
        return None, errors, warnings
    connector = row["connector_key"].lower() or infer_connector(url)
    if not connector:
        connector = "assisted"
        warnings.append("connector unknown: catalogued as assisted (a person posts) until a connector is chosen")
    if connector not in known:
        errors.append(f"connector_key: '{connector}' is not a known connector ({', '.join(sorted(known))})")
        return None, errors, warnings
    out = {"name": row["name"], "url": url, "connector_key": connector,
           "platform_key": row["platform_key"].lower() or connector, "surface": row["surface"] or None,
           "category": row["category"] or None, "geo": row["geo"] or None, "rules_url": row["rules_url"] or None,
           "description": row["description"] or None,
           "audience_tags": [t.strip() for t in re.split(r"[;,|]", row["audience_tags"]) if t.strip()],
           "frequency_cap": _cap(row["frequency_cap"], errors), "link_policy": _links(row["link_policy"], errors),
           "self_promo_allowed": _bool(row["self_promo_allowed"], "self_promo_allowed", errors),
           "disclosure_required": _bool(row["disclosure_required"], "disclosure_required", errors),
           "automation_allowed": _bool(row["automation_allowed"], "automation_allowed", errors),
           "relevance_reason": row["relevance_reason"] or None, "params": derive_params(connector, url)}
    for field in ("est_audience", "relevance_score"):
        v = row[field].replace(",", "").replace("_", "")
        if v:
            if v.isdigit():
                out[field] = int(v)
            else:
                errors.append(f"{field}: '{row[field]}' is not a whole number")
    if out.get("relevance_score") is not None and not 1 <= out["relevance_score"] <= 5:
        errors.append("relevance_score: must be 1-5")
    detected, confidence = detect(f"{out['name']}. {out['description'] or ''}")
    declared = primary_lang(row["language"])
    if declared and not re.fullmatch(r"[a-z]{2}", declared):
        errors.append(f"language: '{row['language']}' is not an ISO 639-1 code")
    out["language"] = declared or detected or "en"
    if not declared and detected:
        warnings.append(f"language not given: detected '{detected}'")
    elif declared and detected and detected != declared and confidence >= 0.5:
        warnings.append(f"language mismatch: declared '{declared}', text looks '{detected}'")
    out["normalized_url"] = normalize_url(url)
    return (None if errors else out), errors, warnings


async def _check_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, row: dict) -> dict:
    async with sem:
        result = {"reachable": False, "http_status": None, "error": None, "est_audience": None, "enriched_from": None}
        try:
            r = await client.head(row["url"])
            if r.status_code in (403, 405, 501) or r.status_code >= 500:
                r = await client.get(row["url"])
            result["http_status"] = r.status_code
            result["reachable"] = r.status_code < 400
        except httpx.HTTPError as e:
            result["error"] = type(e).__name__
        if not result["reachable"]:
            return result
        params = row["params"]
        try:
            if row["connector_key"] == "lemmy" and params.get("community"):
                data = (await client.get(f"{params['base_url']}/api/v3/community",
                                         params={"name": params["community"]})).json()
                result["est_audience"] = data["community_view"]["counts"]["subscribers"]
                result["enriched_from"] = "lemmy community counts"
            elif row["connector_key"] == "discourse":
                data = (await client.get(f"{params['base_url']}/about.json")).json()
                result["est_audience"] = data["about"]["stats"]["users_count"]
                result["enriched_from"] = "discourse about.json"
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass  # enrichment is best-effort; the row is still imported
        return result


async def check_all(rows: list[dict]) -> list[dict]:
    s = get_settings()
    sem = asyncio.Semaphore(s.catalogue_concurrency)
    async with httpx.AsyncClient(timeout=s.catalogue_reachability_timeout_s, follow_redirects=True,
                                 headers={"User-Agent": s.crawler_user_agent}) as client:
        return await asyncio.gather(*(_check_one(client, sem, r) for r in rows))


def read_csv(data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AppError(ErrorCode.CATALOGUE_INPUT_INVALID, "CSV must be UTF-8")
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    if not {"name", "url"} <= headers:
        raise AppError(ErrorCode.CATALOGUE_INPUT_INVALID, "CSV needs at least the columns name and url",
                       {"columns": sorted(headers)})
    rows = [{(k or "").strip().lower(): v for k, v in r.items()} for r in reader]
    if len(rows) > MAX_ROWS:
        raise AppError(ErrorCode.CATALOGUE_INPUT_INVALID, f"At most {MAX_ROWS} rows per import", {"rows": len(rows)})
    return rows


@handler("catalogue_import")
def run_import(ctx: JobContext) -> dict:
    p = ctx.payload
    t0 = time.monotonic()
    storage = get_storage()
    if p.get("csv_url"):  # files in by URL: fetched here, kept as evidence next to the report
        guard_url(p["csv_url"])
        try:
            r = httpx.get(p["csv_url"], timeout=30, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise AppError(ErrorCode.CATALOGUE_INPUT_INVALID, f"Could not download the CSV ({type(e).__name__})",
                           {"csv_url": p["csv_url"]})
        if len(r.content) > 20_000_000:
            raise AppError(ErrorCode.CATALOGUE_INPUT_INVALID, "CSV is larger than 20 MB")
        p["input_key"] = f"imports/{ctx.job_id}/input.csv"
        storage.put_bytes(p["input_key"], r.content, "text/csv")
    raw_rows = read_csv(storage.get_bytes(p["input_key"]))
    known = set(all_connectors())
    report: list[dict] = []
    valid: list[tuple[int, dict, list[str]]] = []
    seen: dict[tuple[str, str], int] = {}
    for n, raw in enumerate(raw_rows, start=2):  # row 1 is the header
        row, errors, warnings = validate_row(n, raw, known)
        entry = {"row": n, "name": (raw.get("name") or "").strip(), "url": (raw.get("url") or "").strip(),
                 "errors": errors, "warnings": warnings}
        if row is None:
            report.append({**entry, "status": "invalid"})
            continue
        key = (row["normalized_url"], row["platform_key"])
        if key in seen:
            report.append({**entry, "status": "duplicate", "normalized_url": row["normalized_url"],
                           "errors": [f"duplicate of row {seen[key]} (same normalised URL and platform)"]})
            continue
        seen[key] = n
        valid.append((n, row, warnings))
    ctx.progress(20)
    timings = {"validate_ms": round((time.monotonic() - t0) * 1000)}

    checks = asyncio.run(check_all([r for _, r, _ in valid])) if p.get("check_reachability", True) else \
        [None] * len(valid)
    timings["reachability_ms"] = round((time.monotonic() - t0) * 1000) - timings["validate_ms"]
    ctx.progress(60)

    provider = get_provider()
    ai_cost, ai_calls, ai_warnings = 0.0, 0, set()
    counts = {"imported": 0, "updated": 0, "existing": 0}
    with session_scope() as db:
        existing = {(d.normalized_url, d.platform_key): d for d in db.execute(select(Destination)).scalars()}
        for (n, row, warnings), check in zip(valid, checks):
            entry = {"row": n, "name": row["name"], "url": row["url"], "normalized_url": row["normalized_url"],
                     "connector_key": row["connector_key"], "language": row["language"], "errors": [],
                     "warnings": warnings}
            if check is not None:
                row["checks"] = {**check, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                entry.update(reachable=check["reachable"], http_status=check["http_status"])
                if not check["reachable"]:
                    warnings.append(f"unreachable ({check['http_status'] or check['error']}): catalogued, "
                                    "but it cannot be certified until it answers")
                if check["est_audience"] is not None and row.get("est_audience") is None:
                    row["est_audience"] = check["est_audience"]
                    warnings.append(f"est_audience enriched from {check['enriched_from']}")
            if p.get("score_relevance", True) and row.get("relevance_score") is None:
                try:
                    res = provider.score_relevance(row)
                    row["relevance_score"], row["relevance_reason"] = res.data["score"], res.data["reason"]
                    ai_cost, ai_calls = ai_cost + res.cost_usd, ai_calls + 1
                    ai_warnings.update(res.warnings)
                except AppError as e:
                    warnings.append(f"relevance not scored: {e.code.value}")
            entry.update(est_audience=row.get("est_audience"), relevance_score=row.get("relevance_score"))
            key = (row["normalized_url"], row["platform_key"])
            data = {k: v for k, v in row.items() if v is not None}
            if key in existing:
                if p.get("update_existing"):
                    d = existing[key]
                    for k, v in data.items():
                        setattr(d, k, v)
                    counts["updated"] += 1
                    report.append({**entry, "status": "updated", "destination_id": d.id})
                else:
                    counts["existing"] += 1
                    report.append({**entry, "status": "exists", "destination_id": existing[key].id,
                                   "errors": ["already in the catalogue (same normalised URL and platform)"]})
                continue
            d = Destination(**data, level="L0")
            db.add(d)
            db.flush()
            existing[key] = d
            counts["imported"] += 1
            report.append({**entry, "status": "imported", "destination_id": d.id})
    ctx.progress(90)

    report.sort(key=lambda r: r["row"])
    summary = {"rows": len(raw_rows), **counts,
               "invalid": sum(r["status"] == "invalid" for r in report),
               "duplicates_in_file": sum(r["status"] == "duplicate" for r in report),
               "unreachable": sum(r.get("reachable") is False for r in report),
               "rows_with_warnings": sum(bool(r["warnings"]) for r in report),
               "seconds": round(time.monotonic() - t0, 2)}
    out_csv = io.StringIO()
    w = csv.DictWriter(out_csv, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in report:
        w.writerow({**r, "errors": "; ".join(r["errors"]), "warnings": "; ".join(r["warnings"])})
    prefix = f"imports/{ctx.job_id}"
    report_url = get_storage().put_bytes(f"{prefix}/report.csv", out_csv.getvalue().encode(), "text/csv")
    timings["total_ms"] = round((time.monotonic() - t0) * 1000)
    manifest = build_manifest(job_id=ctx.job_id, kind="catalogue_import", timings_ms=timings,
                              providers=[{"task": "relevance", "provider": provider.name, "model": provider.model,
                                          "calls": ai_calls, "cost_usd": round(ai_cost, 6)}],
                              cost_estimate_usd=ai_cost, warnings=sorted(ai_warnings),
                              extra_settings={k: p.get(k) for k in ("check_reachability", "score_relevance",
                                                                    "update_existing")})
    result = {"summary": summary, "manifest": manifest}
    summary_url = put_json(f"{prefix}/summary.json", {**result, "rows": report})
    log.info("catalogue imported", extra=summary)
    return {**result, "links": {"report_csv": report_url, "summary": summary_url}}
