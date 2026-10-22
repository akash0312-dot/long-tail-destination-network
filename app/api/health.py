from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.connectors.registry import all_connectors
from app.db import get_engine
from app.storage import get_storage

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/ready", summary="Readiness: database, storage and connectors")
def ready():
    checks: dict[str, str] = {}
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
    checks["storage"] = "ok" if get_storage().ready() else "error"
    try:
        checks["connectors"] = f"ok ({len(all_connectors())})"
    except Exception as e:
        checks["connectors"] = f"error: {type(e).__name__}"
    ok = all(v.startswith("ok") for v in checks.values())
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)
