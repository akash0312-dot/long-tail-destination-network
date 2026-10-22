"""Certification harness: a destination counts only after a real test post went live and was cleaned up.

Native / protocol tier:  rules recorded -> publish test post -> live URL answers -> get_status = published
                         -> delete -> get_status = deleted, all within CERTIFICATION_MAX_CLEANUP_S (10 min)
Assisted tier:           rules recorded -> build a pack -> its files are downloadable, deep link + checklist present
Pass  -> level L1 (assisted) / L2 (feed, bridge) / L3 (native, protocol), certified, last_certified_at = now.
Fail  -> demoted to L0, certification_status = failed (excluded from publishing), alert raised.
Whatever fails, a test post that was created is always deleted (cleanup step), so certification never leaves spam.
"""
import logging
import time
import uuid

import httpx

from app.config import get_settings
from app.connectors.registry import get_connector
from app.connectors.router import call_method
from app.db import session_scope
from app.errors import AppError, ErrorCode
from app.jobs import JobContext, handler
from app.models import CertificationRun, Connection, Destination, Publication, utcnow
from app.output_manifest import build_manifest
from app.services.publishing import connection_ref, destination_ref
from app.storage import get_storage, put_json

log = logging.getLogger("app.certification")
TIER_LEVEL = {"assisted": "L1", "feed": "L2", "bridge": "L2", "native": "L3", "protocol": "L3"}
SNAPSHOT_BYTES = 200_000


class StepFailed(Exception):
    pass


class Run:
    def __init__(self):
        self.steps: list[dict] = []

    def step(self, name: str, fn, check=None):
        t = time.monotonic()
        try:
            out = fn()
            problem = check(out) if check else None
        except AppError as e:
            self.steps.append({"step": name, "ok": False, "ms": _ms(t), "error": e.to_dict()["error"]})
            raise StepFailed(name)
        self.steps.append({"step": name, "ok": problem is None, "ms": _ms(t), "result": _safe(out),
                           **({"problem": problem} if problem else {})})
        if problem:
            raise StepFailed(name)
        return out


def _ms(t: float) -> int:
    return round((time.monotonic() - t) * 1000)


def _safe(out):
    return out if isinstance(out, (dict, list, str, int, float, bool, type(None))) else str(out)


def test_rendition(dest: Destination, run_id: str, surface) -> dict:
    """Neutral, link-free, clearly labelled test content that satisfies the destination's rules and format
    (video platforms and feeds get the configured test video)."""
    r = {"title": f"Connector certification test {run_id[:8]} - please ignore",
         "body": "Automated connectivity test from the Long-Tail Destination Network. "
                 "This post is deleted within seconds.",
         "content_type": "text" if "text" in surface.content_types else surface.content_types[0],
         "language": dest.language or "en", "ai_generated": False, "is_promotional": False}
    if surface.spec.min_video:
        r["media"] = [{"url": get_settings().certification_test_video_url, "type": "video", "mime_type": "video/mp4"}]
    return r


def rules_recorded(dest: Destination) -> dict:
    missing = [f for f in ("frequency_cap", "link_policy") if getattr(dest, f) is None]
    if missing:
        raise AppError(ErrorCode.CERTIFICATION_FAILED, "Posting rules are not recorded for this destination",
                       {"missing": missing})
    return {"frequency_cap": dest.frequency_cap, "link_policy": dest.link_policy,
            "self_promo_allowed": dest.self_promo_allowed, "disclosure_required": dest.disclosure_required,
            "automation_allowed": dest.automation_allowed, "language": dest.language, "rules_url": dest.rules_url}


def fetch_live(url: str) -> dict:
    """The live URL must answer 2xx. Federated servers (Lemmy without its web UI) answer ActivityPub instead."""
    attempts = []
    s = get_settings()
    if s.internal_base_url and url.startswith(s.public_base_url.rstrip("/")):  # a page we host ourselves (feeds)
        url = s.internal_base_url.rstrip("/") + url[len(s.public_base_url.rstrip("/")):]
    for accept in ("text/html,application/xhtml+xml", "application/activity+json"):
        try:
            r = httpx.get(url, headers={"Accept": accept}, timeout=10, follow_redirects=True)
        except httpx.HTTPError as e:
            attempts.append({"accept": accept, "error": type(e).__name__})
            continue
        attempts.append({"accept": accept, "status": r.status_code})
        if r.is_success:
            return {"status": r.status_code, "accept": accept, "content_type": r.headers.get("content-type"),
                    "bytes": len(r.content), "body": r.content[:SNAPSHOT_BYTES], "attempts": attempts}
    return {"status": None, "attempts": attempts}


def _alert(dest: Destination, run_id: str, reason: str, evidence_url: str | None) -> str:
    alert = {"type": "destination_demoted", "destination_id": dest.id, "destination": dest.name,
             "connector": dest.connector_key, "certification_run_id": run_id, "reason": reason,
             "evidence": evidence_url, "at": utcnow().isoformat(), "action": "Investigate, fix, then recertify"}
    url = put_json(f"alerts/{dest.id}/{run_id}.json", alert)
    log.warning("destination demoted", extra={"destination_id": dest.id, "reason": reason, "alert": url})
    hook = get_settings().alert_webhook_url
    if hook:
        try:
            httpx.post(hook, json=alert, timeout=5)
        except httpx.HTTPError as e:
            log.warning("alert webhook failed", extra={"error": type(e).__name__})
    return url


@handler("certification")
def run_certification(ctx: JobContext) -> dict:
    s = get_settings()
    dest_id = ctx.payload["destination_id"]
    with session_scope() as db:
        dest = db.get(Destination, dest_id)
        if dest is None:
            raise AppError(ErrorCode.NOT_FOUND, "Destination not found", {"destination_id": dest_id})
        conn = db.get(Connection, dest.connection_id) if dest.connection_id else None
    connector = get_connector(dest.connector_key)
    tier = connector.manifest.tier
    run_id = ctx.job_id
    run = Run()
    started = time.monotonic()
    dref, cref = destination_ref(dest).model_dump(), connection_ref(conn).model_dump()
    published: dict | None = None
    publish_attempted = deleted = False
    snapshot_url = None
    key = f"cert-{run_id}"
    rendition = test_rendition(dest, run_id, connector.manifest.surface(dest.surface))
    try:
        run.step("rules_recorded", lambda: rules_recorded(dest))
        run.step("format_limits_recorded", lambda: connector.manifest.surface(dest.surface).model_dump())
        ctx.progress(10)
        if conn is not None:
            run.step("connection_active", lambda: {"status": conn.status},
                     lambda r: None if r["status"] == "active" else f"connection is {r['status']}; reconnect first")

        if tier == "assisted":
            pack = run.step("build_assist_pack", lambda: call_method(dest.connector_key, "build_assist_pack",
                                                                     {"rendition": rendition, "destination": dref}),
                            lambda p: None if p.get("deep_link") and p.get("checklist") else "deep link or "
                                                                                          "checklist missing")
            run.step("pack_files_downloadable", lambda: _pack_files_ok(pack))
        else:
            publish_attempted = True
            published = run.step("publish_test_post", lambda: call_method(
                dest.connector_key, "publish", {"rendition": rendition, "destination": dref, "connection": cref,
                                                "idempotency_key": key}),
                lambda r: None if r.get("platform_post_id") and r.get("live_url") else "no post id or live URL")
            _ledger(dest.id, key, ctx.job_id, published, rendition)
            ctx.progress(40)
            live = run.step("confirm_live_url", lambda: fetch_live(published["live_url"]),
                            lambda r: None if r.get("status") else "live URL did not answer 2xx")
            ext = "json" if "json" in (live.get("content_type") or "") else "html"
            snapshot_url = get_storage().put_bytes(f"certifications/{dest.id}/{run_id}/live_snapshot.{ext}",
                                                   live.pop("body"), live.get("content_type") or "text/html")
            ref = {"platform_post_id": published["platform_post_id"], "connection": cref, "destination": dref}
            run.step("status_published", lambda: call_method(dest.connector_key, "get_status", ref),
                     lambda r: None if r.get("status") == "published" else f"status is {r.get('status')}")
            ctx.progress(60)
            run.step("delete_test_post", lambda: call_method(dest.connector_key, "delete", ref),
                     lambda r: None if r.get("deleted") else "platform did not confirm deletion")
            deleted = True
            run.step("status_deleted", lambda: call_method(dest.connector_key, "get_status", ref),
                     lambda r: None if r.get("status") in ("deleted", "removed") else f"still {r.get('status')}")
            elapsed = round(time.monotonic() - started, 1)
            run.step("cleaned_within_limit", lambda: {"seconds": elapsed, "limit_s": s.certification_max_cleanup_s},
                     lambda r: None if r["seconds"] <= r["limit_s"] else "cleanup took too long")
        passed = True
    except StepFailed:
        passed = False
    finally:
        # only after a publish was attempted: recovering a lost post re-publishes with the same key, which would
        # otherwise create a test post that never needed to exist
        if publish_attempted and not deleted:
            _cleanup(run, dest, dref, cref, key, published, rendition)
    ctx.progress(90)
    return _finish(ctx, dest, connector, run, passed, time.monotonic() - started, snapshot_url, tier)


def _pack_files_ok(pack: dict) -> list[str]:
    """The pack's own files (caption, pack.json) must exist in storage. Read by key: the download links point at
    the public endpoint, which is not always reachable from inside the worker."""
    storage = get_storage()
    for f in pack["files"]:
        if f.get("kind") not in ("caption", "pack"):
            continue
        try:
            if not storage.get_bytes(f["storage_key"]):
                raise ValueError("empty file")
        except Exception as e:
            raise AppError(ErrorCode.CERTIFICATION_FAILED, f"Pack file {f['name']} is missing from storage",
                           {"error": type(e).__name__})
    return [f["name"] for f in pack["files"]]


def _ledger(dest_id, key, job_id, published, rendition):
    with session_scope() as db:
        db.add(Publication(destination_id=dest_id, idempotency_key=key, job_id=job_id, status="published",
                           is_test=True, platform_post_id=published["platform_post_id"],
                           live_url=published["live_url"], rendition=rendition))


def _cleanup(run: Run, dest, dref, cref, key, published, rendition) -> None:
    """Delete the test post even after a failure. If its id never reached us (timeout, renamed field), publish
    again with the same idempotency key: the connector's reconcile step returns the existing post, then delete it."""
    try:
        post_id = published["platform_post_id"] if published else None
        if post_id is None:
            again = call_method(dest.connector_key, "publish", {"rendition": rendition, "destination": dref,
                                                                "connection": cref, "idempotency_key": key})
            post_id = again["platform_post_id"]
        call_method(dest.connector_key, "delete", {"platform_post_id": post_id, "connection": cref,
                                                   "destination": dref})
        run.steps.append({"step": "cleanup", "ok": True, "result": {"deleted_post_id": post_id}})
    except AppError as e:
        run.steps.append({"step": "cleanup", "ok": False, "error": e.to_dict()["error"],
                          "problem": "Test post may remain; delete it by hand (see alert)"})
    except Exception as e:  # cleanup must never hide the original failure
        run.steps.append({"step": "cleanup", "ok": False, "problem": f"{type(e).__name__}: {e}"[:300]})


def _finish(ctx, dest, connector, run: Run, passed: bool, duration: float, snapshot_url, tier) -> dict:
    run_record = {"certification_run_id": ctx.job_id, "destination_id": dest.id, "destination": dest.name,
                  "connector": {"key": connector.key, "version": connector.manifest.version, "tier": tier},
                  "passed": passed, "duration_s": round(duration, 2), "steps": run.steps,
                  "manifest": build_manifest(job_id=ctx.job_id, kind="certification",
                                             timings_ms={"total": round(duration * 1000)},
                                             connectors=[{"key": connector.key,
                                                          "version": connector.manifest.version}])}
    evidence_url = put_json(f"certifications/{dest.id}/{ctx.job_id}/run.json", run_record)
    failed = next((st for st in run.steps if not st["ok"] and st["step"] != "cleanup"), None)
    reason = None
    if failed:
        reason = f"{failed['step']}: " + (failed.get("problem") or failed.get("error", {}).get("message", "failed"))
    alert_url = None
    with session_scope() as db:
        d = db.get(Destination, dest.id)
        if passed:
            d.level, d.certification_status, d.last_certified_at = TIER_LEVEL.get(tier, "L1"), "certified", utcnow()
        else:
            d.level, d.certification_status = "L0", "failed"
        level = d.level
        db.add(CertificationRun(id=uuid.uuid4().hex, destination_id=dest.id, job_id=ctx.job_id, passed=passed,
                                steps=run.steps, evidence_url=evidence_url, duration_s=round(duration, 2)))
    if not passed:
        alert_url = _alert(dest, ctx.job_id, reason, evidence_url)
    links = {"evidence": evidence_url, **({"live_snapshot": snapshot_url} if snapshot_url else {}),
             **({"alert": alert_url} if alert_url else {})}
    if not passed:
        raise AppError(ErrorCode.CERTIFICATION_FAILED, f"Certification failed at {reason}; destination demoted to L0",
                       {"destination_id": dest.id, "level": level, "steps": run.steps, "links": links})
    return {"certification_run_id": ctx.job_id, "destination_id": dest.id, "passed": True, "level": level,
            "duration_s": round(duration, 2), "steps": run.steps, "links": links}
