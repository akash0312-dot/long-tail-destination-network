"""Publication orchestrator:
certification gate -> posting rules (block / reschedule / fix disclosure) -> validate -> publish -> ledger -> manifest."""
import logging
import time
from datetime import timedelta

from sqlalchemy import select

from app import rules as rules_engine
from app.config import get_settings
from app.connectors.base import violation_error
from app.connectors.registry import get_connector
from app.connectors.router import call_method
from app.connectors.schemas import ConnectionRef, DestinationRef
from app.db import session_scope
from app.errors import AppError, ErrorCode
from app.jobs import JobContext, create_job, handler
from app.models import Connection, Destination, Publication, utcnow
from app.output_manifest import build_manifest
from app.storage import put_json

log = logging.getLogger("app.publishing")


def destination_ref(d: Destination) -> DestinationRef:
    return DestinationRef(id=d.id, name=d.name, url=d.url, surface=d.surface, language=d.language,
                          params=dict(d.params or {}), rules_url=d.rules_url,
                          self_promo_allowed=d.self_promo_allowed, link_policy=d.link_policy,
                          disclosure_required=d.disclosure_required, automation_allowed=d.automation_allowed,
                          frequency_cap=d.frequency_cap)


def recent_post_times(destination_id: str, cap: dict | None) -> list:
    """Real (non-test) posts inside the cap window, from our own ledger."""
    if not cap:
        return []
    since = utcnow() - rules_engine.cap_window(cap)
    with session_scope() as db:
        rows = db.execute(select(Publication.created_at).where(
            Publication.destination_id == destination_id, Publication.status == "published",
            Publication.is_test.is_(False), Publication.created_at >= since)).scalars().all()
    return [t if t.tzinfo else t.replace(tzinfo=utcnow().tzinfo) for t in rows]


def connection_ref(c: Connection | None) -> ConnectionRef:
    if c is None:
        return ConnectionRef()
    return ConnectionRef(id=c.id, auth_type=c.auth_type, secret_refs=dict(c.secret_refs or {}),
                         params=dict(c.params or {}))


def load_targets(destination_id: str) -> tuple[Destination, Connection | None]:
    with session_scope() as db:
        dest = db.get(Destination, destination_id)
        if dest is None:
            raise AppError(ErrorCode.NOT_FOUND, "Destination not found", {"destination_id": destination_id})
        conn = db.get(Connection, dest.connection_id) if dest.connection_id else None
        if conn is not None and conn.status != "active":
            raise AppError(ErrorCode.CONNECTION_AUTH_EXPIRED,
                           f"Connection is {conn.status}; the owner must reconnect before posting",
                           {"connection_id": conn.id, "connection_status": conn.status})
        return dest, conn


def _record(dest_id: str, key: str, job_id: str, status: str, rendition: dict, *, is_test=False, result=None,
            error=None, warnings=None) -> str:
    with session_scope() as db:
        pub = Publication(destination_id=dest_id, idempotency_key=key, job_id=job_id, status=status,
                          rendition=rendition, is_test=is_test, error=error, warnings=warnings or [],
                          platform_post_id=(result or {}).get("platform_post_id"),
                          live_url=(result or {}).get("live_url"))
        db.add(pub)
        db.flush()
        return pub.id


def _mark_connection(conn_id: str | None, status: str) -> None:
    if not conn_id:
        return
    with session_scope() as db:
        c = db.get(Connection, conn_id)
        if c:
            c.status = status


def _assist_pack(ctx: JobContext, dest: Destination, connector_key: str, rendition: dict, key: str, is_test: bool,
                 warnings: list[str], checks: list[dict]) -> dict:
    t0 = time.monotonic()
    pack = call_method("assisted", "build_assist_pack",
                       {"rendition": rendition, "destination": destination_ref(dest).model_dump()})
    warnings = warnings + pack.get("warnings", [])
    if connector_key != "assisted":
        warnings.append(f"ASSIST_PACK: automated posting is not allowed at this destination; a person must post "
                        f"(the '{connector_key}' connector was not used)")
    pub_id = _record(dest.id, key, ctx.job_id, "awaiting_human", rendition, is_test=is_test, warnings=warnings)
    manifest = build_manifest(job_id=ctx.job_id, kind="assist_pack", warnings=warnings,
                              timings_ms={"total": round((time.monotonic() - t0) * 1000)},
                              connectors=[{"key": "assisted", "version": get_connector("assisted").manifest.version}],
                              ai_generated=bool(rendition.get("ai_generated")))
    out = {"publication_id": pub_id, "destination_id": dest.id, "status": "awaiting_human", "platform_post_id": None,
           "live_url": None, "assist_pack": pack, "warnings": warnings, "rule_checks": checks, "manifest": manifest}
    out["links"] = {"manifest": put_json(f"publications/{pub_id}/result.json", out),
                    **{f["name"]: f["url"] for f in pack["files"] if f.get("kind") in ("caption", "pack")}}
    return out


@handler("publication")
def run_publication(ctx: JobContext) -> dict:
    p = ctx.payload
    dest_id, key, rendition = p["destination_id"], p["idempotency_key"], p["rendition"]
    timings: dict[str, int] = {}
    t0 = time.monotonic()

    # Layer 3 of idempotency: our own ledger already has a successful post for this key
    with session_scope() as db:
        prior = db.execute(select(Publication).where(Publication.destination_id == dest_id,
                                                     Publication.idempotency_key == key,
                                                     Publication.status == "published")).scalars().first()
        if prior:
            return {"publication_id": prior.id, "platform_post_id": prior.platform_post_id,
                    "live_url": prior.live_url, "status": "published",
                    "warnings": ["IDEMPOTENT_REPLAY: already published for this idempotency key"]}

    is_test = p.get("is_test", False)
    dest, conn = load_targets(dest_id)
    s = get_settings()
    if dest.certification_status == "failed" or (s.require_certification and not is_test
                                                  and dest.certification_status != "certified"):
        raise AppError(ErrorCode.DEST_NOT_CERTIFIED,
                       f"Destination is {dest.certification_status}; only certified destinations receive posts",
                       {"destination_id": dest_id, "certification_status": dest.certification_status,
                        "level": dest.level})
    connector = get_connector(dest.connector_key)
    # where automation is not allowed, a person posts from an assist pack instead
    assisted = connector.manifest.tier == "assisted" or dest.automation_allowed is False
    rules = rules_engine.rules_of(dest)
    if assisted:
        rules["automation_allowed"] = None

    # posting rules: checked before every post
    decision = rules_engine.evaluate(rendition, rules, recent_post_times(dest_id, dest.frequency_cap), utcnow(),
                                     s.own_domain_list(), is_test=is_test)
    checks = [c.as_dict() for c in decision.checks]
    timings["rules"] = round((time.monotonic() - t0) * 1000)
    if decision.action == "block":
        _record(dest_id, key, ctx.job_id, "blocked", rendition, is_test=is_test,
                error={"code": decision.code, "reason": decision.reason, "checks": checks})
        raise AppError(ErrorCode(decision.code), decision.reason, {"checks": checks})
    if decision.action == "reschedule":
        details = {"checks": checks, "retry_at": decision.not_before.isoformat(),
                   "retry_after_s": max(1, int((decision.not_before - utcnow()).total_seconds()))}
        if p.get("on_cap", "reschedule") == "reschedule":
            follow_up = create_job("publication", p, run_after=decision.not_before + timedelta(seconds=1))
            details["rescheduled_job_id"] = follow_up.id
        _record(dest_id, key, ctx.job_id, "rescheduled" if "rescheduled_job_id" in details else "blocked",
                rendition, is_test=is_test, error={"code": decision.code, "reason": decision.reason, **details})
        message = decision.reason + ("; post rescheduled" if "rescheduled_job_id" in details else "")
        raise AppError(ErrorCode.DEST_RULE_FREQUENCY_CAP, message, details)
    rendition = decision.rendition  # may carry an appended AI disclosure
    rule_warnings = list(decision.warnings)
    if assisted:
        return _assist_pack(ctx, dest, connector.key, rendition, key, is_test, rule_warnings, checks)

    body = {"rendition": rendition, "destination": destination_ref(dest).model_dump(),
            "connection": connection_ref(conn).model_dump(), "idempotency_key": key}
    ctx.progress(20)

    v = call_method(dest.connector_key, "validate", body)
    timings["validate"] = round((time.monotonic() - t0) * 1000)
    if not v["ok"]:
        err = violation_error(v["violations"])
        _record(dest_id, key, ctx.job_id, "blocked", rendition, is_test=is_test,
                error={"code": err.code.value, "violations": v["violations"]})
        raise err
    ctx.progress(40)

    t1 = time.monotonic()
    try:
        result = call_method(dest.connector_key, "publish", body)
    except AppError as e:
        if e.code == ErrorCode.CONNECTION_AUTH_EXPIRED:
            _mark_connection(conn.id if conn else None, "expired")
        _record(dest_id, key, ctx.job_id, "failed", rendition, is_test=is_test, error=e.to_dict()["error"])
        raise
    timings["publish"] = round((time.monotonic() - t1) * 1000)
    warnings = rule_warnings + list(result.get("warnings", []))
    pub_id = _record(dest_id, key, ctx.job_id, "published", rendition, is_test=is_test,
                     result=result, warnings=warnings)
    ctx.progress(90)

    timings["total"] = round((time.monotonic() - t0) * 1000)
    manifest = build_manifest(job_id=ctx.job_id, kind="publication", warnings=warnings, timings_ms=timings,
                              connectors=[{"key": connector.key, "version": connector.manifest.version}],
                              ai_generated=bool(rendition.get("ai_generated")))
    out = {"publication_id": pub_id, "destination_id": dest_id, "platform_post_id": result["platform_post_id"],
           "live_url": result["live_url"], "status": result["status"], "warnings": warnings,
           "rule_checks": checks, "manifest": manifest}
    manifest_url = put_json(f"publications/{pub_id}/result.json", out)
    out["links"] = {"manifest": manifest_url, "live_url": result["live_url"]}
    log.info("published", extra={"destination_id": dest_id, "connector": connector.key,
                                 "platform_post_id": result["platform_post_id"]})
    return out
