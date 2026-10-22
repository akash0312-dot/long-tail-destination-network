"""Publication orchestrator: load destination + connection -> validate -> publish -> ledger -> manifest."""
import logging
import time

from sqlalchemy import select

from app.connectors.registry import get_connector
from app.connectors.router import call_method
from app.connectors.schemas import ConnectionRef, DestinationRef
from app.db import session_scope
from app.errors import AppError, ErrorCode
from app.jobs import JobContext, handler
from app.models import Connection, Destination, Publication
from app.output_manifest import build_manifest
from app.storage import put_json

log = logging.getLogger("app.publishing")


def destination_ref(d: Destination) -> DestinationRef:
    return DestinationRef(id=d.id, name=d.name, url=d.url, surface=d.surface, language=d.language,
                          params=dict(d.params or {}))


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

    dest, conn = load_targets(dest_id)
    connector = get_connector(dest.connector_key)
    body = {"rendition": rendition, "destination": destination_ref(dest).model_dump(),
            "connection": connection_ref(conn).model_dump(), "idempotency_key": key}
    ctx.progress(20)

    v = call_method(dest.connector_key, "validate", body)
    timings["validate"] = round((time.monotonic() - t0) * 1000)
    if not v["ok"]:
        _record(dest_id, key, ctx.job_id, "blocked", rendition, is_test=p.get("is_test", False),
                error={"code": "DEST_SPEC_VIOLATION", "violations": v["violations"]})
        raise AppError(ErrorCode.DEST_SPEC_VIOLATION, "Content breaks the destination's format limits",
                       {"violations": v["violations"]})
    ctx.progress(40)

    t1 = time.monotonic()
    try:
        result = call_method(dest.connector_key, "publish", body)
    except AppError as e:
        if e.code == ErrorCode.CONNECTION_AUTH_EXPIRED:
            _mark_connection(conn.id if conn else None, "expired")
        _record(dest_id, key, ctx.job_id, "failed", rendition, is_test=p.get("is_test", False),
                error=e.to_dict()["error"])
        raise
    timings["publish"] = round((time.monotonic() - t1) * 1000)
    warnings = list(result.get("warnings", []))
    pub_id = _record(dest_id, key, ctx.job_id, "published", rendition, is_test=p.get("is_test", False),
                     result=result, warnings=warnings)
    ctx.progress(90)

    timings["total"] = round((time.monotonic() - t0) * 1000)
    manifest = build_manifest(job_id=ctx.job_id, kind="publication", warnings=warnings, timings_ms=timings,
                              connectors=[{"key": connector.key, "version": connector.manifest.version}],
                              ai_generated=bool(rendition.get("ai_generated")))
    out = {"publication_id": pub_id, "destination_id": dest_id, "platform_post_id": result["platform_post_id"],
           "live_url": result["live_url"], "status": result["status"], "warnings": warnings, "manifest": manifest}
    manifest_url = put_json(f"publications/{pub_id}/result.json", out)
    out["links"] = {"manifest": manifest_url, "live_url": result["live_url"]}
    log.info("published", extra={"destination_id": dest_id, "connector": connector.key,
                                 "platform_post_id": result["platform_post_id"]})
    return out
