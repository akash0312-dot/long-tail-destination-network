"""Mock of the PeerTube REST API — OAuth password grant, video import, status, description, delete."""
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from mock_platforms import state

router = APIRouter()
P = "peertube"


def _err(status: int, detail: str, code: str | None = None):
    return JSONResponse({"status": status, "detail": detail, **({"code": code} if code else {})}, status_code=status)


def _fault(f):
    if f is None:
        return None
    if f.kind == "auth_expired":
        return _err(401, "Token is invalid", "invalid_token")
    if f.kind == "reject":
        return _err(400, f.message or "Video import rejected by the instance")
    return state.error_fault_response(f, "json")


def _authed(request: Request, inst: str) -> bool:
    h = request.headers.get("authorization", "")
    return h.startswith("Bearer ") and h[7:] in state.instance(P, inst).users


def _view(v: dict, f=None) -> dict:
    out = {"id": v["id"], "uuid": v["uuid"], "shortUUID": v["shortUUID"], "name": v["name"],
           "state": {"id": 1, "label": "Published"}}
    return state.rename(out, "shortUUID", "short_uuid") if f and f.kind == "rename_field" else out


@router.get("/{inst:path}/api/v1/oauth-clients/local")
async def oauth_client(inst: str):
    f = state.take_fault(P, inst, "login")
    if f and (r := _fault(f)):
        return r
    return {"client_id": f"client-{inst}", "client_secret": "mock-client-secret"}


@router.post("/{inst:path}/api/v1/users/token")
async def token(inst: str, request: Request):
    f = state.take_fault(P, inst, "login")
    if f and (r := _fault(f)):
        return r
    form = await request.form()
    if form.get("password") in ("", "wrong-password"):
        return _err(400, "Invalid grant: user credentials are invalid", "invalid_grant")
    tok = uuid.uuid4().hex
    state.instance(P, inst).users[tok] = {"username": form.get("username")}
    return {"token_type": "Bearer", "access_token": tok, "refresh_token": uuid.uuid4().hex, "expires_in": 86400}


@router.get("/{inst:path}/api/v1/users/me/videos")
async def my_videos(inst: str, request: Request):
    if not _authed(request, inst):
        return _err(401, "Token is invalid", "invalid_token")
    f = state.take_fault(P, inst, "find_existing")
    if f and (r := _fault(f)):
        return r
    vids = sorted((v for v in state.instance(P, inst).posts.values() if not v["deleted"]),
                  key=lambda v: v["id"], reverse=True)
    return {"total": len(vids), "data": [_view(v) for v in vids]}


@router.post("/{inst:path}/api/v1/videos/imports")
async def import_video(inst: str, request: Request):
    if not _authed(request, inst):
        return _err(401, "Token is invalid", "invalid_token")
    f = state.take_fault(P, inst, "publish")
    if f and (r := _fault(f)):
        return r
    form = await request.form()
    if not form.get("targetUrl") or not form.get("channelId"):
        return _err(400, "Incorrect request parameters: targetUrl, channelId")
    vid = state.next_id()
    v = {"id": vid, "uuid": str(uuid.uuid4()), "shortUUID": uuid.uuid4().hex[:22], "name": form.get("name"),
         "description": form.get("description", ""), "deleted": False}
    state.instance(P, inst).posts[vid] = v
    await state.apply_delay(f)
    return {"id": state.next_id(), "state": {"id": 1}, "video": _view(v, f)}


@router.get("/{inst:path}/api/v1/videos/{vid:int}/description")
async def description(inst: str, vid: int):
    v = state.instance(P, inst).posts.get(vid)
    return {"description": v["description"]} if v and not v["deleted"] else _err(404, "Video not found")


@router.get("/{inst:path}/api/v1/videos/{vid:int}")
async def get_video(inst: str, vid: int, request: Request):
    f = state.take_fault(P, inst, "get_status")
    if f and (r := _fault(f)):
        return r
    await state.apply_delay(f)
    v = state.instance(P, inst).posts.get(vid)
    return _view(v, f) if v and not v["deleted"] else _err(404, "Video not found")


@router.delete("/{inst:path}/api/v1/videos/{vid:int}")
async def delete_video(inst: str, vid: int, request: Request):
    if not _authed(request, inst):
        return _err(401, "Token is invalid", "invalid_token")
    f = state.take_fault(P, inst, "delete")
    if f and (r := _fault(f)):
        return r
    v = state.instance(P, inst).posts.get(vid)
    if not v or v["deleted"]:
        return _err(404, "Video not found")
    v["deleted"] = True
    return Response(status_code=204)


@router.get("/{inst:path}/w/{short}")
async def live(inst: str, short: str):
    v = next((v for v in state.instance(P, inst).posts.values() if v["shortUUID"] == short), None)
    if v and not v["deleted"]:
        return HTMLResponse(f"<html><body><h1>{v['name']}</h1></body></html>")
    return HTMLResponse("not found", status_code=404)
