"""Mock partner site implementing webhook protocol v1 (docs/PARTNER_WEBHOOK.md), including signature checks."""
import hashlib
import hmac
import json
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from mock_platforms import state

router = APIRouter()
P = "webhook"
PREFIXES = ("/sim/webhook", "/partner")  # simulation-mode rewrite, and direct use from compose


def _secret() -> str:
    return os.environ.get("MOCK_WEBHOOK_SECRET", "sim-signing_secret")


def _verify(request: Request, body: bytes) -> JSONResponse | None:
    ts, sig = request.headers.get("x-signature-timestamp", ""), request.headers.get("x-signature", "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > 300:
        return JSONResponse({"error": "stale_or_missing_timestamp"}, status_code=401)
    expected = "sha256=" + hmac.new(_secret().encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return JSONResponse({"error": "invalid_signature"}, status_code=401)
    return None


def _fault(f):
    if f is None:
        return None
    if f.kind == "auth_expired":
        return JSONResponse({"error": "permission_revoked"}, status_code=403)
    if f.kind == "reject":
        return JSONResponse({"error": f.message or "content_not_accepted"}, status_code=422)
    return state.error_fault_response(f, "json")


def _view(request: Request, inst: str, post: dict, f=None) -> dict:
    body = {"id": post["id"], "url": f"{str(request.base_url).rstrip('/')}/__live/partner/{inst}/{post['id']}",
            "status": "published"}
    return state.rename(body, "id", "post_id") if f and f.kind == "rename_field" else body


async def create(request: Request, inst: str):
    body = await request.body()
    if r := _verify(request, body):
        return r
    f = state.take_fault(P, inst, "publish")
    if f and (r := _fault(f)):
        return r
    db = state.instance(P, inst)
    key = request.headers.get("idempotency-key")
    existing = next((p for p in db.posts.values() if key and p["idempotency_key"] == key and not p["deleted"]), None)
    if existing:
        return JSONResponse(_view(request, inst, existing), status_code=200, headers={"Idempotent-Replayed": "true"})
    data = json.loads(body)
    pid = state.next_id()
    db.posts[pid] = {"id": pid, "idempotency_key": key, "post": data.get("post", {}), "deleted": False}
    await state.apply_delay(f)
    return JSONResponse(_view(request, inst, db.posts[pid], f), status_code=201)


async def read(request: Request, inst: str, pid: int):
    if r := _verify(request, b""):
        return r
    f = state.take_fault(P, inst, "get_status")
    if f and (r := _fault(f)):
        return r
    await state.apply_delay(f)
    post = state.instance(P, inst).posts.get(pid)
    if not post or post["deleted"]:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return _view(request, inst, post, f)


async def remove(request: Request, inst: str, pid: int):
    if r := _verify(request, b""):
        return r
    f = state.take_fault(P, inst, "delete")
    if f and (r := _fault(f)):
        return r
    post = state.instance(P, inst).posts.get(pid)
    if not post or post["deleted"]:
        return JSONResponse({"error": "not_found"}, status_code=404)
    post["deleted"] = True
    return Response(status_code=204)


for prefix in PREFIXES:
    ns = prefix.strip("/")

    def _bind(ns=ns, prefix=prefix):
        @router.post(f"{prefix}/{{name}}")
        async def _create(name: str, request: Request):
            return await create(request, f"{ns}/{name}")

        @router.get(f"{prefix}/{{name}}/{{pid:int}}")
        async def _read(name: str, pid: int, request: Request):
            return await read(request, f"{ns}/{name}", pid)

        @router.delete(f"{prefix}/{{name}}/{{pid:int}}")
        async def _remove(name: str, pid: int, request: Request):
            return await remove(request, f"{ns}/{name}", pid)

    _bind()


@router.get("/__live/partner/{inst:path}/{pid:int}")
async def live(inst: str, pid: int):
    post = state.instance(P, inst).posts.get(pid)
    if post and not post["deleted"]:
        return HTMLResponse(f"<html><body><h1>{post['post'].get('title', '')}</h1></body></html>")
    return HTMLResponse("not found", status_code=404)
