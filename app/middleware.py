"""Request context (correlation id, auth, rate limit, access log) and Idempotency-Key handling."""
import hashlib
import logging
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.db import session_scope
from app.errors import ErrorCode, error_body
from app.logging_setup import correlation_id_var
from app.models import IdempotencyRecord

log = logging.getLogger("app.access")
PROTECTED_PREFIXES = ("/api/v1/", "/connector/v1")
PUBLIC_PREFIXES = ("/api/v1/feeds/",)  # MRSS feeds are pulled by aggregators that cannot send our token


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = {}

    def _rate_limited(self, client: str) -> float | None:
        limit = get_settings().rate_limit_per_minute
        if limit <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            win = self._hits.setdefault(client, deque())
            while win and now - win[0] >= 60:
                win.popleft()
            if len(win) >= limit:
                return 60 - (now - win[0])
            win.append(now)
        return None

    async def dispatch(self, request, call_next):
        cid = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex
        token = correlation_id_var.set(cid)
        started = time.monotonic()
        try:
            s = get_settings()
            path = request.url.path
            if path.startswith(PROTECTED_PREFIXES) and not path.startswith(PUBLIC_PREFIXES):
                if s.api_token and request.headers.get("Authorization") != f"Bearer {s.api_token}":
                    return self._finish(JSONResponse(error_body(ErrorCode.UNAUTHORIZED, "Missing or invalid API token"),
                                                     status_code=401), cid, request, started)
                client = request.headers.get("Authorization") or (request.client.host if request.client else "?")
                wait = self._rate_limited(client)
                if wait is not None:
                    body = error_body(ErrorCode.RATE_LIMITED, "Too many requests", {"retry_after_s": round(wait, 1)})
                    return self._finish(JSONResponse(body, status_code=429, headers={"Retry-After": str(int(wait) + 1)}),
                                        cid, request, started)
            response = await call_next(request)
            return self._finish(response, cid, request, started)
        finally:
            correlation_id_var.reset(token)

    @staticmethod
    def _finish(response, cid, request, started):
        response.headers["X-Correlation-Id"] = cid
        log.info("request", extra={"method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "duration_ms": round((time.monotonic() - started) * 1000)})
        return response


def _claim(key: str, scope: str, req_hash: str):
    """Atomically reserve the key. Returns ("new",), ("replay", status, body), ("conflict",) or ("in_progress",)."""
    ttl = timedelta(hours=get_settings().idempotency_ttl_hours)
    try:
        with session_scope() as db:
            existing = db.get(IdempotencyRecord, {"key": key, "scope": scope})
            if existing is not None:
                created = existing.created_at if existing.created_at.tzinfo else existing.created_at.replace(
                    tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - created > ttl:
                    db.delete(existing)
                    db.flush()
                else:
                    if existing.request_hash != req_hash:
                        return ("conflict",)
                    if existing.state == "done":
                        return ("replay", existing.status_code, existing.response_body)
                    return ("in_progress",)
            db.add(IdempotencyRecord(key=key, scope=scope, request_hash=req_hash, state="in_progress"))
        return ("new",)
    except IntegrityError:
        return _claim(key, scope, req_hash)  # lost a race; re-read the winner's record


def _store(key: str, scope: str, status: int, body: bytes | None) -> None:
    with session_scope() as db:
        rec = db.get(IdempotencyRecord, {"key": key, "scope": scope})
        if rec is None:
            return
        if body is None:  # server error: release the key so the client can retry
            db.delete(rec)
        else:
            rec.state, rec.status_code, rec.response_body = "done", status, body.decode()


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Every POST under /api/v1/ needs an Idempotency-Key. Same key + same body -> the stored response (same job).
    Same key + different body -> 409 IDEMPOTENCY_KEY_CONFLICT. The work is never done twice."""

    async def dispatch(self, request, call_next):
        if request.method != "POST" or not request.url.path.startswith("/api/v1/"):
            return await call_next(request)
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 200:
            return JSONResponse(error_body(ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
                                           "POST requests need an Idempotency-Key header (1-200 chars)"),
                                status_code=400)
        body = await request.body()
        scope = f"POST {request.url.path}"
        req_hash = hashlib.sha256(scope.encode() + b"\n" + body).hexdigest()
        claim = await run_in_threadpool(_claim, key, scope, req_hash)
        if claim[0] == "conflict":
            return JSONResponse(error_body(ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                                           "This Idempotency-Key was already used with a different request body"),
                                status_code=409)
        if claim[0] == "in_progress":
            return JSONResponse(error_body(ErrorCode.IDEMPOTENCY_REQUEST_IN_PROGRESS,
                                           "A request with this Idempotency-Key is still being processed"),
                                status_code=409, headers={"Retry-After": "1"})
        if claim[0] == "replay":
            return Response(content=claim[2], status_code=claim[1], media_type="application/json",
                            headers={"Idempotent-Replayed": "true"})
        try:
            response = await call_next(request)
            data = b"".join([chunk async for chunk in response.body_iterator])
        except Exception:
            await run_in_threadpool(_store, key, scope, 500, None)
            raise
        await run_in_threadpool(_store, key, scope, response.status_code,
                                data if response.status_code < 500 else None)
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(content=data, status_code=response.status_code, headers=headers,
                        media_type=response.media_type)
