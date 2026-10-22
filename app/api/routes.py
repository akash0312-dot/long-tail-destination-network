"""/api/v1 routes: catalogue (connections, destinations), publications, jobs, files."""
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import recommender
from app.ai import get_provider
from app.api.schemas import (CertificationCreate, ConnectionCreate, ConnectionUpdate, DestinationCreate,
                             DestinationUpdate, PublicationCreate, RecommendationRequest, RulesCaptureCreate,
                             RuleReview, TailorRequest)
from app.output_manifest import build_manifest
from app.catalogue.rules_capture import review
from app.connectors.schemas import ConnectionRef, Rendition
from app.tailoring import tailor
from app.config import get_settings
from app.connectors.registry import all_connectors, get_connector
from app.connectors.router import call_method
from app.db import get_db
from app.errors import AppError, ErrorCode
from app.jobs import create_job, job_view
from app.models import CertificationRun, Connection, Destination, Job, Publication, RuleProposal, Ticket, utcnow
from app.services.scheduler import recertify_due
from app.services.publishing import connection_ref, destination_ref
from app.storage import LocalStorage, get_storage
from app.urls import normalize_url

router = APIRouter(prefix="/api/v1")


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _connection_view(c: Connection) -> dict:
    d = _row(c)
    d["secret_refs"] = {k: v.split(":", 1)[0] + ":***" for k, v in (c.secret_refs or {}).items()}
    return d


# ---------------------------------------------------------------- connectors
@router.get("/connectors", tags=["catalogue"], summary="Available connectors")
def list_connectors() -> dict:
    disabled = get_settings().disabled_connector_set()
    return {"connectors": [{"key": k, "version": c.manifest.version, "tier": c.manifest.tier,
                            "capabilities": c.manifest.capabilities, "enabled": k not in disabled}
                           for k, c in sorted(all_connectors().items())]}


# ---------------------------------------------------------------- connections
@router.post("/connections", status_code=201, tags=["catalogue"], summary="Register a connection (secret refs only)")
def create_connection(body: ConnectionCreate, db: Session = Depends(get_db)) -> dict:
    get_connector(body.connector_key, check_enabled=False)
    c = Connection(**body.model_dump())
    db.add(c)
    db.flush()
    return _connection_view(c)


@router.get("/connections", tags=["catalogue"])
def list_connections(db: Session = Depends(get_db)) -> dict:
    return {"items": [_connection_view(c) for c in db.execute(select(Connection)).scalars()]}


@router.post("/connections/{connection_id}/reconnect", tags=["catalogue"],
             summary="Verify credentials (refresh_auth where supported) and reactivate the connection")
def reconnect_connection(connection_id: str, db: Session = Depends(get_db)) -> dict:
    c = db.get(Connection, connection_id)
    if c is None:
        raise AppError(ErrorCode.NOT_FOUND, "Connection not found")
    connector = get_connector(c.connector_key, check_enabled=False)
    dest = db.execute(select(Destination).where(Destination.connection_id == c.id)).scalars().first()
    verified, expires_at = False, None
    if "refresh_auth" in connector.manifest.capabilities:
        result = call_method(c.connector_key, "refresh_auth", {
            "connection": connection_ref(c).model_dump(),
            "destination": destination_ref(dest).model_dump() if dest else None})
        verified, expires_at = True, result.get("expires_at")
    c.status = "active"
    return {"connection_id": c.id, "status": c.status, "verified": verified, "expires_at": expires_at,
            "note": None if verified else "This connector cannot check credentials directly; run a certification."}


@router.patch("/connections/{connection_id}", tags=["catalogue"], summary="Update status (e.g. revoked) or refs")
def update_connection(connection_id: str, body: ConnectionUpdate, db: Session = Depends(get_db)) -> dict:
    c = db.get(Connection, connection_id)
    if c is None:
        raise AppError(ErrorCode.NOT_FOUND, "Connection not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    return _connection_view(c)


# ---------------------------------------------------------------- destinations
@router.post("/destinations", status_code=201, tags=["catalogue"], summary="Add one destination (one catalogue row)")
def create_destination(body: DestinationCreate, db: Session = Depends(get_db)) -> dict:
    connector = get_connector(body.connector_key, check_enabled=False)
    param = connector.manifest.instance_param
    data = body.model_dump()
    data["platform_key"] = data["platform_key"] or body.connector_key
    if param and param.endswith("url") and param not in data["params"]:
        data["params"][param] = body.url  # e.g. base_url / endpoint_url default to the destination URL
    data["normalized_url"] = normalize_url(body.url)
    dup = db.execute(select(Destination).where(Destination.normalized_url == data["normalized_url"],
                                               Destination.platform_key == data["platform_key"])).scalar_one_or_none()
    if dup:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Destination already exists (same normalised URL and platform)",
                       {"existing_id": dup.id, "normalized_url": dup.normalized_url})
    if body.connection_id and db.get(Connection, body.connection_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "connection_id does not exist")
    d = Destination(**data)
    db.add(d)
    db.flush()
    return _row(d)


@router.get("/destinations", tags=["catalogue"])
def list_destinations(connector_key: str | None = None, level: str | None = None,
                      db: Session = Depends(get_db)) -> dict:
    q = select(Destination)
    if connector_key:
        q = q.where(Destination.connector_key == connector_key)
    if level:
        q = q.where(Destination.level == level)
    return {"items": [_row(d) for d in db.execute(q.order_by(Destination.created_at)).scalars()]}


@router.get("/destinations/{destination_id}", tags=["catalogue"])
def get_destination(destination_id: str, db: Session = Depends(get_db)) -> dict:
    d = db.get(Destination, destination_id)
    if d is None:
        raise AppError(ErrorCode.NOT_FOUND, "Destination not found")
    return _row(d)


@router.patch("/destinations/{destination_id}", tags=["catalogue"])
def update_destination(destination_id: str, body: DestinationUpdate, db: Session = Depends(get_db)) -> dict:
    d = db.get(Destination, destination_id)
    if d is None:
        raise AppError(ErrorCode.NOT_FOUND, "Destination not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(d, k, v)
    return _row(d)


# ---------------------------------------------------------------- publications
@router.post("/publications", status_code=202, tags=["publishing"],
             summary="Publish one rendition to one destination (background job)")
def create_publication(body: PublicationCreate, request: Request, db: Session = Depends(get_db)):
    if db.get(Destination, body.destination_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "Destination not found", {"destination_id": body.destination_id})
    api_key = request.headers["Idempotency-Key"]
    payload = {"destination_id": body.destination_id, "rendition": body.rendition.model_dump(),
               "idempotency_key": f"{api_key}:{body.destination_id}", "is_test": body.is_test,
               "on_cap": body.on_cap, "tailor": body.tailor}
    job = create_job("publication", payload, str(body.callback_url) if body.callback_url else None)
    return JSONResponse(jsonable_encoder(job_view(job)), status_code=202,
                        headers={"Location": f"/api/v1/jobs/{job.id}"})


@router.get("/publications/{publication_id}", tags=["publishing"])
def get_publication(publication_id: str, db: Session = Depends(get_db)) -> dict:
    p = db.get(Publication, publication_id)
    if p is None:
        raise AppError(ErrorCode.NOT_FOUND, "Publication not found")
    return _row(p)


# ---------------------------------------------------------------- catalogue import, rules capture, relevance
@router.post("/catalogue/imports", status_code=202, tags=["catalogue"],
             summary="Import a CSV of destinations (upload `file` or give `csv_url`); row-by-row report (job)")
def import_catalogue(file: UploadFile | None = File(default=None), csv_url: str | None = Form(default=None),
                     check_reachability: bool = Form(default=True), score_relevance: bool = Form(default=True),
                     update_existing: bool = Form(default=False)):
    if (file is None) == (csv_url is None):
        raise AppError(ErrorCode.CATALOGUE_INPUT_INVALID, "Send exactly one of: file (CSV upload) or csv_url")
    payload = {"check_reachability": check_reachability, "score_relevance": score_relevance,
               "update_existing": update_existing}
    if file is not None:
        key = f"imports/uploads/{uuid.uuid4().hex}.csv"
        get_storage().put_bytes(key, file.file.read(), "text/csv")
        payload["input_key"] = key
    else:
        payload["csv_url"] = csv_url
    job = create_job("catalogue_import", payload)
    return JSONResponse(jsonable_encoder(job_view(job)), status_code=202, headers={"Location": f"/api/v1/jobs/{job.id}"})


@router.post("/destinations/{destination_id}/rules/capture", status_code=202, tags=["catalogue"],
             summary="Read the destination's rules page and propose posting rules for a person to confirm (job)")
def capture_rules(destination_id: str, body: RulesCaptureCreate, db: Session = Depends(get_db)):
    if db.get(Destination, destination_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "Destination not found")
    job = create_job("rules_capture", {"destination_id": destination_id,
                                       "rules_url": str(body.rules_url) if body.rules_url else None})
    return JSONResponse(jsonable_encoder(job_view(job)), status_code=202, headers={"Location": f"/api/v1/jobs/{job.id}"})


@router.get("/rule-proposals", tags=["catalogue"], summary="Rule proposals waiting for (or after) review")
def list_rule_proposals(destination_id: str | None = None, status: str | None = None,
                        db: Session = Depends(get_db)) -> dict:
    q = select(RuleProposal).order_by(RuleProposal.created_at.desc())
    if destination_id:
        q = q.where(RuleProposal.destination_id == destination_id)
    if status:
        q = q.where(RuleProposal.status == status)
    return {"items": [_row(r) for r in db.execute(q.limit(200)).scalars()]}


@router.post("/rule-proposals/{proposal_id}/confirm", tags=["catalogue"],
             summary="A person confirms the proposed rules (optionally corrected); only now are they applied")
def confirm_rule_proposal(proposal_id: str, body: RuleReview) -> dict:
    return review(proposal_id, "confirmed", body.reviewed_by, body.overrides, body.note)


@router.post("/rule-proposals/{proposal_id}/reject", tags=["catalogue"], summary="Reject a rule proposal")
def reject_rule_proposal(proposal_id: str, body: RuleReview) -> dict:
    return review(proposal_id, "rejected", body.reviewed_by, None, body.note)


@router.post("/destinations/{destination_id}/relevance", tags=["catalogue"],
             summary="Score the destination 1-5 against the target audience, with a reason")
def score_destination(destination_id: str, db: Session = Depends(get_db)) -> dict:
    d = db.get(Destination, destination_id)
    if d is None:
        raise AppError(ErrorCode.NOT_FOUND, "Destination not found")
    res = get_provider().score_relevance(_row(d))
    d.relevance_score, d.relevance_reason = res.data["score"], res.data["reason"]
    return {"destination_id": d.id, "relevance_score": d.relevance_score, "relevance_reason": d.relevance_reason,
            "provider": res.manifest_entry("relevance"), "warnings": res.warnings}


# ---------------------------------------------------------------- recommendations
@router.post("/recommendations", tags=["publishing"],
             summary="Rank destinations for one content item by audience, rules and past results, with reasons")
def recommend(body: RecommendationRequest, db: Session = Depends(get_db)) -> dict:
    dests = [_row(d) for d in db.execute(select(Destination)).scalars()]
    history: dict[str, list[dict]] = {}
    for p in db.execute(select(Publication).where(Publication.status == "published")).scalars():
        created = p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=utcnow().tzinfo)
        history.setdefault(p.destination_id, []).append(
            {"created_at": created, "status": p.status, "is_test": p.is_test, "metrics": p.metrics})
    surfaces = {k: sorted({t for s in c.manifest.surfaces for t in s.content_types})
                for k, c in all_connectors().items()}
    ranked = recommender.rank(body.content.model_dump(), dests, history, utcnow(), surfaces,
                              get_settings().own_domain_list())
    eligible = [recommender.as_dict(c) for c in ranked if not c.excluded][: body.limit]
    out = {"items": eligible, "considered": len(dests),
           "excluded_count": sum(1 for c in ranked if c.excluded),
           "manifest": build_manifest(job_id=None, kind="recommendation",
                                      providers=[{"task": "ranking", "provider": "rules+metrics", "model": "v1"}])}
    if body.include_excluded:
        out["excluded"] = [recommender.as_dict(c) for c in ranked if c.excluded]
    return out


# ---------------------------------------------------------------- tailoring
@router.post("/tailor", tags=["publishing"],
             summary="Preview one content item tailored for each destination, with the changes and validation")
def tailor_content(body: TailorRequest, db: Session = Depends(get_db)) -> dict:
    items = []
    for dest_id in body.destination_ids:
        d = db.get(Destination, dest_id)
        if d is None:
            raise AppError(ErrorCode.NOT_FOUND, "Destination not found", {"destination_id": dest_id})
        connector = get_connector(d.connector_key, check_enabled=False)
        rendition, changes = tailor(body.content.model_dump(), d, d.connector_key, connector.manifest.surface(d.surface))
        validation = connector.validate(Rendition.model_validate(rendition), destination_ref(d), ConnectionRef())
        items.append({"destination_id": d.id, "destination": d.name, "connector_key": d.connector_key,
                      "rendition": rendition, "changes": changes, "validation": validation})
    return {"items": items}


# ---------------------------------------------------------------- certification
@router.post("/certifications", status_code=202, tags=["certification"],
             summary="Certify a destination: real test post, live check, status, delete, evidence (job)")
def create_certification(body: CertificationCreate, db: Session = Depends(get_db)):
    if db.get(Destination, body.destination_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "Destination not found", {"destination_id": body.destination_id})
    job = create_job("certification", {"destination_id": body.destination_id},
                     str(body.callback_url) if body.callback_url else None)
    return JSONResponse(jsonable_encoder(job_view(job)), status_code=202, headers={"Location": f"/api/v1/jobs/{job.id}"})


@router.post("/recertifications/run", tags=["certification"],
             summary="Queue certification for every destination due (the worker also does this on a schedule)")
def run_recertification() -> dict:
    return recertify_due()


@router.get("/tickets", tags=["certification"], summary="Tickets opened when destinations were demoted")
def list_tickets(status: str | None = None, destination_id: str | None = None,
                 db: Session = Depends(get_db)) -> dict:
    q = select(Ticket).order_by(Ticket.created_at.desc())
    if status:
        q = q.where(Ticket.status == status)
    if destination_id:
        q = q.where(Ticket.destination_id == destination_id)
    return {"items": [_row(t) for t in db.execute(q.limit(200)).scalars()]}


@router.get("/certifications", tags=["certification"], summary="Certification runs with evidence links")
def list_certifications(destination_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    q = select(CertificationRun).order_by(CertificationRun.created_at.desc())
    if destination_id:
        q = q.where(CertificationRun.destination_id == destination_id)
    return {"items": [_row(r) for r in db.execute(q.limit(200)).scalars()]}


@router.post("/publications/{publication_id}/metrics", tags=["publishing"],
             summary="Fetch normalised metrics from the platform and store them on the publication")
def refresh_metrics(publication_id: str, db: Session = Depends(get_db)) -> dict:
    p = db.get(Publication, publication_id)
    if p is None:
        raise AppError(ErrorCode.NOT_FOUND, "Publication not found")
    if p.status != "published" or not p.platform_post_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Publication is {p.status}; only published posts have metrics")
    dest = db.get(Destination, p.destination_id)
    connector = get_connector(dest.connector_key)
    if "metrics" not in connector.manifest.capabilities:
        raise AppError(ErrorCode.METHOD_NOT_SUPPORTED, f"Connector '{dest.connector_key}' has no metrics")
    conn = db.get(Connection, dest.connection_id) if dest.connection_id else None
    m = call_method(dest.connector_key, "fetch_metrics", {
        "platform_post_id": p.platform_post_id, "since": None, "connection": connection_ref(conn).model_dump(),
        "destination": destination_ref(dest).model_dump()})
    p.metrics, p.metrics_at = m, utcnow()
    return {"publication_id": p.id, "metrics": m, "fetched_at": p.metrics_at,
            "unavailable": sorted(k for k, v in m.items() if v is None)}


# ---------------------------------------------------------------- jobs
@router.get("/jobs/{job_id}", tags=["jobs"], summary="Job status, progress and result links")
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise AppError(ErrorCode.NOT_FOUND, "Job not found", {"job_id": job_id})
    return job_view(job)


# ---------------------------------------------------------------- MRSS feeds (public: aggregators pull them)
@router.get("/feeds/{feed_key}.xml", tags=["feeds"], summary="Public MRSS feed of an mrss destination",
            response_class=Response)
def get_feed(feed_key: str):
    try:
        data = get_storage().get_bytes(f"feeds/{feed_key}/feed.xml")
    except Exception:
        raise AppError(ErrorCode.NOT_FOUND, "Feed not found", {"feed_key": feed_key})
    return Response(content=data, media_type="application/rss+xml")


# ---------------------------------------------------------------- files (local storage backend only)
@router.get("/files/{key:path}", tags=["files"], summary="Download an output file (local storage backend)")
def get_file(key: str):
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise AppError(ErrorCode.NOT_FOUND, "Files are served from object storage via signed links")
    try:
        data = storage.get_bytes(key)
    except (FileNotFoundError, ValueError):
        raise AppError(ErrorCode.NOT_FOUND, "File not found")
    media = "application/json" if key.endswith(".json") else "application/octet-stream"
    return Response(content=data, media_type=media)
