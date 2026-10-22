"""/api/v1 routes: catalogue (connections, destinations), publications, jobs, files."""
from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (CertificationCreate, ConnectionCreate, ConnectionUpdate, DestinationCreate,
                             DestinationUpdate, PublicationCreate)
from app.config import get_settings
from app.connectors.registry import all_connectors, get_connector
from app.connectors.router import call_method
from app.db import get_db
from app.errors import AppError, ErrorCode
from app.jobs import create_job, job_view
from app.models import CertificationRun, Connection, Destination, Job, Publication
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
               "on_cap": body.on_cap}
    job = create_job("publication", payload, str(body.callback_url) if body.callback_url else None)
    return JSONResponse(jsonable_encoder(job_view(job)), status_code=202,
                        headers={"Location": f"/api/v1/jobs/{job.id}"})


@router.get("/publications/{publication_id}", tags=["publishing"])
def get_publication(publication_id: str, db: Session = Depends(get_db)) -> dict:
    p = db.get(Publication, publication_id)
    if p is None:
        raise AppError(ErrorCode.NOT_FOUND, "Publication not found")
    return _row(p)


# ---------------------------------------------------------------- certification
@router.post("/certifications", status_code=202, tags=["certification"],
             summary="Certify a destination: real test post, live check, status, delete, evidence (job)")
def create_certification(body: CertificationCreate, db: Session = Depends(get_db)):
    if db.get(Destination, body.destination_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "Destination not found", {"destination_id": body.destination_id})
    job = create_job("certification", {"destination_id": body.destination_id},
                     str(body.callback_url) if body.callback_url else None)
    return JSONResponse(jsonable_encoder(job_view(job)), status_code=202, headers={"Location": f"/api/v1/jobs/{job.id}"})


@router.get("/certifications", tags=["certification"], summary="Certification runs with evidence links")
def list_certifications(destination_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    q = select(CertificationRun).order_by(CertificationRun.created_at.desc())
    if destination_id:
        q = q.where(CertificationRun.destination_id == destination_id)
    return {"items": [_row(r) for r in db.execute(q.limit(200)).scalars()]}


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
