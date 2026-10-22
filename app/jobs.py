"""Background jobs: POST returns 202 + job_id; GET /api/v1/jobs/{id} shows status and progress.

Executors: "worker" (separate process polls the jobs table), "inprocess" (thread pool in the API process),
"sync" (runs before the 202 is returned; used by tests for determinism).
"""
import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.db import session_scope
from app.errors import AppError, ErrorCode
from app.logging_setup import correlation_id_var, job_id_var
from app.models import Job, utcnow

log = logging.getLogger("app.jobs")
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="job")


@dataclass
class JobContext:
    job_id: str
    payload: dict[str, Any]

    def progress(self, pct: int) -> None:
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            job.progress = max(0, min(100, int(pct)))


HANDLERS: dict[str, Callable[[JobContext], dict]] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def job_view(job: Job) -> dict:
    result = job.result or {}
    links = {"self": f"/api/v1/jobs/{job.id}"}
    links.update(result.get("links", {}) if isinstance(result, dict) else {})
    return {"job_id": job.id, "kind": job.kind, "status": job.status, "progress": job.progress,
            "result": job.result, "error": job.error, "links": links,
            "created_at": job.created_at, "started_at": job.started_at, "finished_at": job.finished_at}


def create_job(kind: str, payload: dict, callback_url: str | None = None) -> Job:
    if kind not in HANDLERS:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"No handler for job kind '{kind}'")
    with session_scope() as db:
        job = Job(kind=kind, payload=payload, callback_url=callback_url, correlation_id=correlation_id_var.get())
        db.add(job)
        db.flush()
        job_id = job.id
    executor = get_settings().job_executor
    if executor == "sync":
        run_job(job_id)
    elif executor == "inprocess":
        _pool.submit(run_job, job_id)
    with session_scope() as db:
        return db.get(Job, job_id)


def run_job(job_id: str) -> None:
    jtok = job_id_var.set(job_id)
    ctok = None
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job.status not in ("queued", "running"):
                return
            job.status, job.started_at, job.attempts = "running", utcnow(), job.attempts + 1
            kind, payload = job.kind, dict(job.payload)
            ctok = correlation_id_var.set(job.correlation_id)
        log.info("job started", extra={"kind": kind})
        status, result, error = "succeeded", None, None
        try:
            result = HANDLERS[kind](JobContext(job_id, payload))
        except AppError as e:
            status, error = "failed", e.to_dict()["error"]
        except Exception as e:  # never fail silently: record what happened
            log.exception("job crashed")
            status, error = "failed", {"code": ErrorCode.INTERNAL_ERROR.value, "message": str(e)[:500],
                                       "retryable": True, "details": {"type": type(e).__name__}}
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status, job.result, job.error, job.finished_at = status, result, error, utcnow()
            if status == "succeeded":
                job.progress = 100
            view = job_view(job)
            callback_url = job.callback_url
        log.info("job finished", extra={"kind": kind, "status": status,
                                        "error_code": error["code"] if error else None})
        if callback_url:
            send_callback(callback_url, view)
    finally:
        job_id_var.reset(jtok)
        if ctok is not None:
            correlation_id_var.reset(ctok)


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def send_callback(url: str, view: dict) -> None:
    s = get_settings()
    body = json.dumps(view, default=str).encode()
    headers = {"Content-Type": "application/json"}
    if s.callback_signing_secret:
        headers[s.callback_signature_header] = sign(body, s.callback_signing_secret)
    else:
        log.warning("callback sent unsigned: CALLBACK_SIGNING_SECRET is not set")
    for attempt in range(3):
        try:
            r = httpx.post(url, content=body, headers=headers, timeout=s.callback_timeout_s)
            if r.is_success:
                return
            log.warning("callback rejected", extra={"status": r.status_code, "attempt": attempt + 1})
        except httpx.HTTPError as e:
            log.warning("callback failed", extra={"error": type(e).__name__, "attempt": attempt + 1})


def claim_next_job() -> str | None:
    """Worker: claim the oldest queued job (SKIP LOCKED on Postgres so several workers can run)."""
    from sqlalchemy import select
    with session_scope() as db:
        job = db.execute(select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
                         .with_for_update(skip_locked=True)).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        return job.id
