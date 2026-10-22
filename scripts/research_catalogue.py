"""Research the real destination catalogue (catalogue only: nothing is ever posted).

    python scripts/research_catalogue.py [candidates.csv] [out_dir]

For every candidate: verify it live (Lemmy/Discourse/PeerTube through their public APIs, the rest over HTTP), read
its rules (Lemmy sidebar via API, PeerTube terms via API, other rules pages via the app's robots.txt-respecting
fetcher), extract posting rules with evidence, fill silent fields with conservative defaults (marked as defaults),
detect language, score relevance. Writes destinations.csv (importable), dropped.csv and RESEARCH.md.

Third-party communities stay automation_allowed=false (permission_status=needed) until their owners agree:
the brief forbids posting to a real community without the owner's permission.
"""
import asyncio
import csv
import html
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import httpx  # noqa: E402

from app.catalogue.heuristics import extract_rules, score_relevance  # noqa: E402
from app.catalogue.language import detect  # noqa: E402
from app.catalogue.rules_capture import fetch_rules_page, html_to_text  # noqa: E402
from app.errors import AppError  # noqa: E402

UA = "LongTailDestinationBot/1.0 (+catalogue research; catalogue only, no posting)"
CONCURRENCY = 8
RULE_FIELDS = ["frequency_cap", "self_promo_allowed", "link_policy", "disclosure_required", "automation_allowed"]
PROMO_FRIENDLY = {"launch", "directory", "showcase", "creator_platform", "video_platform", "feed_directory"}
DEFAULTS = {  # conservative operating policy when the rules page is silent
    "lemmy": {"frequency_cap": "1/7d", "self_promo_allowed": False, "link_policy": "max:1"},
    "discourse": {"frequency_cap": "1/7d", "self_promo_allowed": False, "link_policy": "max:1"},
    "devto": {"frequency_cap": "2/7d", "self_promo_allowed": True, "link_policy": "max:3"},
    "hashnode": {"frequency_cap": "2/7d", "self_promo_allowed": True, "link_policy": "max:3"},
    "peertube": {"frequency_cap": "3/7d", "self_promo_allowed": True, "link_policy": "max:3"},
    "mrss": {"frequency_cap": "10/1d", "self_promo_allowed": True, "link_policy": "any"},
    "assisted": {"frequency_cap": "1/30d", "link_policy": "max:1"},
}
OWN_ACCOUNT = {"devto", "hashnode", "peertube", "mrss"}  # we post to our own account/feed through a public API
COLUMNS = ["name", "url", "connector_key", "platform_key", "category", "audience_tags", "language", "geo",
           "est_audience", "rules_url", "frequency_cap", "self_promo_allowed", "link_policy", "disclosure_required",
           "automation_allowed", "relevance_score", "relevance_reason", "level", "last_certified_at", "description",
           "verification", "http_status", "verified_at", "rules_source", "rules_extracted_fields",
           "rules_defaults_applied", "rules_evidence", "permission_status", "notes"]
META_DESC = re.compile(r"<meta[^>]+(?:name|property)=[\"'](?:og:)?description[\"'][^>]*content=[\"']([^\"']{10,})"
                       r"|<meta[^>]+content=[\"']([^\"']{10,})[\"'][^>]*(?:name|property)=[\"'](?:og:)?description",
                       re.IGNORECASE)


class Drop(Exception):
    pass


def clean(text: str, n: int = 300) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)|\[([^\]]*)\]\([^)]*\)", r"\1", text or "")
    return re.sub(r"\s+", " ", re.sub(r"[#*_>`|]", " ", text)).strip()[:n]


def fmt_cap(cap: dict | None) -> str | None:
    if not cap:
        return None
    return f"{cap['posts']}/{cap['per_days']}d" if "per_days" in cap else f"{cap['posts']}/{cap['per_hours']}h"


def fmt_links(policy: dict | None) -> str | None:
    if not policy:
        return None
    if policy.get("allowed") is False:
        return "none"
    return f"max:{policy['max_links']}" if policy.get("max_links") is not None else "any"


async def get(client, url, **kw):
    try:
        return await client.get(url, **kw)
    except httpx.HTTPError as e:
        raise Drop(f"unreachable ({type(e).__name__})")


async def rules_page(url: str) -> str:
    try:
        return await asyncio.to_thread(fetch_rules_page, url)
    except AppError as e:
        return f"__unavailable__ {e.message}"


async def verify(client, row) -> dict:
    url, key = row["url"], row["connector_key"]
    p = urlsplit(url)
    info = {"description": "", "est_audience": None, "rules_text": "", "rules_url": row.get("rules_url") or "",
            "verification": "", "http_status": None, "notes": []}
    if key == "lemmy":
        base, name = f"{p.scheme}://{p.netloc}", p.path.rstrip("/").split("/c/")[-1]
        r = await get(client, f"{base}/api/v3/community", params={"name": name})
        info["http_status"] = r.status_code
        if r.status_code != 200 or "community_view" not in r.text:
            raise Drop(f"community not found (HTTP {r.status_code})")
        cv = r.json()["community_view"]
        c = cv["community"]
        if c.get("nsfw"):
            raise Drop("NSFW community")
        if c.get("deleted") or c.get("removed"):
            raise Drop("community deleted or removed")
        if c.get("posting_restricted_to_mods"):
            raise Drop("only moderators can post")
        info.update(description=clean(f"{c.get('title', '')}. {c.get('description') or ''}"),
                    est_audience=cv.get("counts", {}).get("subscribers"), rules_text=c.get("description") or "",
                    rules_url=url, verification="verified via Lemmy API (community exists, open to posts)")
    elif key == "discourse":
        r = await get(client, f"{url.rstrip('/')}/about.json", headers={"Accept": "application/json"})
        info["http_status"] = r.status_code
        if r.status_code != 200 or '"about"' not in r.text:
            raise Drop(f"not verifiable as a Discourse forum (about.json HTTP {r.status_code})")
        about = r.json()["about"]
        stats = about.get("stats") or {}
        info.update(description=clean(f"{about.get('title', '')}. {about.get('description') or ''}"),
                    est_audience=stats.get("users_count") or stats.get("user_count"),
                    verification="verified via Discourse about.json")
        info["rules_url"] = info["rules_url"] or f"{url.rstrip('/')}/guidelines"
        info["rules_text"] = await rules_page(info["rules_url"])
    elif key == "peertube":
        r = await get(client, f"{url.rstrip('/')}/api/v1/config/about")
        info["http_status"] = r.status_code
        if r.status_code != 200 or '"instance"' not in r.text:
            raise Drop(f"not verifiable as a PeerTube instance (HTTP {r.status_code})")
        inst = r.json()["instance"]
        stats = await get(client, f"{url.rstrip('/')}/api/v1/server/stats")
        users = stats.json().get("totalUsers") if stats.status_code == 200 else None
        info.update(description=clean(f"{inst.get('name', '')}. {inst.get('shortDescription') or ''}"),
                    est_audience=users, rules_text=f"{inst.get('terms') or ''}\n{inst.get('codeOfConduct') or ''}",
                    rules_url=f"{url.rstrip('/')}/about/instance", verification="verified via PeerTube API")
    else:
        r = await get(client, url, headers={"Accept": "text/html"})
        info["http_status"] = r.status_code
        if r.status_code in (401, 403, 429) or (r.status_code == 503 and "cloudflare" in r.text.lower()):
            info["verification"] = f"site exists; bot protection answered HTTP {r.status_code}"
        elif r.status_code < 400:
            info["verification"] = "reachable over HTTP"
            m = META_DESC.search(r.text[:200_000])
            if m:
                info["description"] = clean(html.unescape(m.group(1) or m.group(2)))
        else:
            raise Drop(f"HTTP {r.status_code}")
        if info["rules_url"]:
            info["rules_text"] = await rules_page(info["rules_url"])
    return info


def build(row: dict, info: dict, now: str) -> dict:
    key, category = row["connector_key"], row["category"]
    rules_text = info["rules_text"] or ""
    unavailable = rules_text.startswith("__unavailable__")
    extracted = extract_rules("" if unavailable else rules_text) if rules_text else {}
    final, got, defaulted = {}, [], []
    defaults = dict(DEFAULTS[key])
    if key == "assisted":
        defaults["self_promo_allowed"] = category in PROMO_FRIENDLY
    defaults["disclosure_required"] = False
    defaults["automation_allowed"] = key in OWN_ACCOUNT
    for f in RULE_FIELDS:
        value = extracted.get(f)
        if f == "frequency_cap":
            value = fmt_cap(value)
        elif f == "link_policy":
            value = fmt_links(value)
        if value is not None:
            final[f] = value
            got.append(f)
        else:
            final[f] = defaults.get(f)
            defaulted.append(f)
    if key not in OWN_ACCOUNT:
        final["automation_allowed"] = False  # third-party community: a person posts until the owner agrees
    lang, conf = detect(info["description"])
    lang = extracted.get("language") or (lang if lang and conf >= 0.3 else "en")
    tags = [t for t in row["audience_tags"].split(";") if t]
    rel = score_relevance({"name": row["name"], "category": category, "description": info["description"],
                           "audience_tags": tags, "url": row["url"], "est_audience": info["est_audience"]})
    evidence = " | ".join(f"{e['field']}: \"{clean(e['quote'], 140)}\"" for e in extracted.get("evidence", [])[:3])
    notes = list(info["notes"])
    if unavailable:
        notes.append(rules_text.replace("__unavailable__ ", "rules page: "))
    return {"name": row["name"], "url": row["url"], "connector_key": key, "platform_key": key, "category": category,
            "audience_tags": ";".join(tags), "language": lang, "geo": "", "est_audience": info["est_audience"] or "",
            "rules_url": info["rules_url"], **{f: ("" if final[f] is None else str(final[f]).lower()
                                                   if isinstance(final[f], bool) else final[f]) for f in RULE_FIELDS},
            "relevance_score": rel["score"], "relevance_reason": rel["reason"], "level": "L0",
            "last_certified_at": "", "description": info["description"], "verification": info["verification"],
            "http_status": info["http_status"], "verified_at": now,
            "rules_source": ("page" if got else "defaults") if rules_text and not unavailable else "defaults",
            "rules_extracted_fields": ";".join(got), "rules_defaults_applied": ";".join(defaulted),
            "rules_evidence": evidence,
            "permission_status": "not_needed (own account or feed)" if key in OWN_ACCOUNT else "needed",
            "notes": "; ".join(notes)}


async def main(candidates: Path, out_dir: Path) -> None:
    rows = list(csv.DictReader(candidates.open(encoding="utf-8")))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sem = asyncio.Semaphore(CONCURRENCY)
    kept, dropped = [], []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as client:
        async def one(row):
            async with sem:
                try:
                    kept.append(build(row, await verify(client, row), now))
                except Drop as e:
                    dropped.append({"name": row["name"], "url": row["url"], "connector_key": row["connector_key"],
                                    "reason": str(e)})
        await asyncio.gather(*(one(r) for r in rows))
    order = {r["url"]: i for i, r in enumerate(rows)}
    kept.sort(key=lambda r: order[r["url"]])
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "destinations.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(kept)
    with (out_dir / "dropped.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "url", "connector_key", "reason"])
        w.writeheader()
        w.writerows(sorted(dropped, key=lambda r: order[r["url"]]))
    write_report(out_dir, rows, kept, dropped, now)
    print(f"candidates {len(rows)}  kept {len(kept)}  dropped {len(dropped)}  -> {out_dir}")


def write_report(out_dir: Path, rows, kept, dropped, now) -> None:
    by_conn = Counter(r["connector_key"] for r in kept)
    rel = Counter(r["relevance_score"] for r in kept)
    src = Counter(r["rules_source"] for r in kept)
    lines = [
        "# Real destination catalogue: research report", "",
        f"Generated {now} by `scripts/research_catalogue.py` from `catalogue_data/candidates.csv`. "
        "Catalogue only: nothing was posted anywhere.", "",
        f"- Candidates: {len(rows)}; **kept: {len(kept)}**; dropped: {len(dropped)} (see `dropped.csv`)",
        f"- Kept by connector: {', '.join(f'{k} {v}' for k, v in sorted(by_conn.items()))}",
        f"- Relevance (1-5): {', '.join(f'{k}: {rel[k]}' for k in sorted(rel))}",
        f"- Rules from the destination's own rules text: {src.get('page', 0)}; defaults only: {src.get('defaults', 0)}",
        "", "## Method", "",
        "1. **Verified live.** Lemmy communities through `/api/v3/community` (dropped if missing, NSFW, removed or "
        "moderator-only); Discourse forums through `/about.json`; PeerTube instances through `/api/v1/config/about`; "
        "everything else over HTTP (a bot-protection 401/403/429 counts as 'site exists' and is recorded as such).",
        "2. **Rules read** from the community's own text: the Lemmy sidebar and PeerTube terms via their APIs, "
        "Discourse `/guidelines`, and the listed rules pages, fetched with the app's fetcher (robots.txt honoured).",
        "3. **Rules extracted** with the deterministic extractor (`app/catalogue/heuristics.py`); each extracted "
        "field has an evidence quote in `rules_evidence`.",
        "4. **Conservative defaults** fill fields the page does not state; they are listed per row in "
        "`rules_defaults_applied`, never presented as the community's own rule.",
        "5. **Permission:** every third-party community is `automation_allowed=false`, `permission_status=needed`: "
        "a person posts (assist pack) until the owner agrees to automated posting.",
        "6. **Relevance 1-5** against AI, video, marketing, SaaS, creators and founders, with the reason.", "",
        "## Defaults when a rules page is silent", "",
        "| Connector | Frequency cap | Self-promotion | Links |", "|---|---|---|---|",
        *[f"| {k} | {v.get('frequency_cap', '-')} | {v.get('self_promo_allowed', 'by category')} | "
          f"{v.get('link_policy', '-')} |" for k, v in DEFAULTS.items()],
        "", "## Limits", "",
        "- Rule extraction is pattern-based; a person should confirm rules before relying on them (the same "
        "confirm step as the rules-capture API).",
        "- Member counts come from the platforms' own APIs where available; other sites have none.",
        "- Verification is a point-in-time check (`verified_at`).",
    ]
    (out_dir / "RESEARCH.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "catalogue_data" / "candidates.csv",
                     Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "catalogue_data"))
