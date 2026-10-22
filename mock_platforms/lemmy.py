"""Mock of the Lemmy 0.19 HTTP API v3 — the subset the connector uses. Instance = URL prefix."""
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from mock_platforms import state

router = APIRouter()
P = "lemmy"


def _err(error: str, status: int = 400):
    return JSONResponse({"error": error}, status_code=status)


def _user(request: Request, inst: str) -> dict | None:
    h = request.headers.get("authorization", "")
    if not h.startswith("Bearer "):
        return None
    token = h[7:]
    for u in state.instance(P, inst).users.values():
        if u["jwt"] == token:
            return u
    return None


def _post_view(post: dict, community: dict, creator: dict) -> dict:
    return {"post": post, "community": community, "creator": creator,
            "counts": {"post_id": post["id"], "comments": 0, "score": 1, "upvotes": 1, "downvotes": 0}}


def _maybe_rename(f, body: dict) -> dict:
    return state.rename(body, "post_view", "postView") if f and f.kind == "rename_field" else body


def _community(db: state.Instance, name: str) -> dict:
    if name not in db.communities:
        db.communities[name] = {"id": state.next_id(), "name": name, "title": name, "local": True}
    return db.communities[name]


@router.post("/{inst:path}/api/v3/user/login")
async def login(inst: str, request: Request):
    f = state.take_fault(P, inst, "login")
    if f and (r := state.error_fault_response(f, "lemmy")):
        return r
    data = await request.json()
    username = data.get("username_or_email", "")
    if data.get("password") in ("", "wrong-password"):
        return _err("incorrect_login")
    db = state.instance(P, inst)
    user = db.users.setdefault(username, {"id": state.next_id(), "name": username})
    user["jwt"] = f"mock.{inst.replace('/', '_')}.{username}.{state.next_id()}"
    return {"jwt": user["jwt"], "registration_created": False, "verify_email_sent": False}


@router.get("/{inst:path}/api/v3/community")
async def get_community(inst: str, name: str):
    return {"community_view": {"community": _community(state.instance(P, inst), name)}}


@router.get("/{inst:path}/api/v3/post/list")
async def list_posts(inst: str, request: Request, community_id: int | None = None, limit: int = 20):
    f = state.take_fault(P, inst, "find_existing")
    if f and (r := state.error_fault_response(f, "lemmy")):
        return r
    db = state.instance(P, inst)
    posts = [p for p in db.posts.values() if community_id is None or p["community_id"] == community_id]
    posts.sort(key=lambda p: p["id"], reverse=True)
    comm = {c["id"]: c for c in db.communities.values()}
    return {"posts": [_post_view(p, comm.get(p["community_id"], {}), {"id": p["creator_id"]})
                      for p in posts[:limit]]}


@router.post("/{inst:path}/api/v3/post")
async def create_post(inst: str, request: Request):
    user = _user(request, inst)
    if not user:
        return _err("not_logged_in")
    f = state.take_fault(P, inst, "publish")
    if f and (r := state.error_fault_response(f, "lemmy")):
        return r
    data = await request.json()
    name = data.get("name", "")
    if not name.strip() or len(name) > 200:
        return _err("invalid_post_title")
    if len(data.get("body") or "") > 50000:
        return _err("invalid_body_field")
    db = state.instance(P, inst)
    comm = next((c for c in db.communities.values() if c["id"] == data.get("community_id")), None)
    if not comm:
        return _err("couldnt_find_community")
    pid = state.next_id()
    base = str(request.base_url).rstrip("/")
    post = {"id": pid, "name": name, "body": data.get("body"), "url": data.get("url"),
            "creator_id": user["id"], "community_id": comm["id"], "deleted": False, "removed": False,
            "published": datetime.now(timezone.utc).isoformat(), "ap_id": f"{base}/{inst}/post/{pid}"}
    db.posts[pid] = post
    await state.apply_delay(f)  # timeout fault: created, but the answer comes too late
    return _maybe_rename(f, {"post_view": _post_view(post, comm, user)})


@router.get("/{inst:path}/api/v3/post")
async def get_post(inst: str, id: int, request: Request):
    f = state.take_fault(P, inst, "get_status")
    if f and (r := state.error_fault_response(f, "lemmy")):
        return r
    await state.apply_delay(f)
    db = state.instance(P, inst)
    post = db.posts.get(id)
    if not post:
        return _err("couldnt_find_post", 404)
    comm = next((c for c in db.communities.values() if c["id"] == post["community_id"]), {})
    return _maybe_rename(f, {"post_view": _post_view(post, comm, {"id": post["creator_id"]})})


@router.post("/{inst:path}/api/v3/post/delete")
async def delete_post(inst: str, request: Request):
    user = _user(request, inst)
    if not user:
        return _err("not_logged_in")
    f = state.take_fault(P, inst, "delete")
    if f and (r := state.error_fault_response(f, "lemmy")):
        return r
    data = await request.json()
    db = state.instance(P, inst)
    post = db.posts.get(int(data.get("post_id", 0)))
    if not post:
        return _err("couldnt_find_post", 404)
    if post["creator_id"] != user["id"]:
        return _err("no_post_edit_allowed")
    post["deleted"] = bool(data.get("deleted", True))
    comm = next((c for c in db.communities.values() if c["id"] == post["community_id"]), {})
    return _maybe_rename(f, {"post_view": _post_view(post, comm, user)})


def live_page(inst: str, pid: int):
    post = state.instance(P, inst).posts.get(pid)
    if post and not post["deleted"]:
        return HTMLResponse(f"<html><body><h1>{post['name']}</h1><p>{post['body'] or ''}</p></body></html>")
    return None
