"""Mock of the Ghost Admin API (v5) — the subset the connector uses. Instance = URL prefix."""
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from mock_platforms import state

router = APIRouter()
P = "ghost"


def _auth_ok(request: Request) -> bool:
    h = request.headers.get("authorization", "")
    return h.startswith("Ghost ") and h[6:].count(".") == 2


def _unauth():
    return JSONResponse({"errors": [{"type": "UnauthorizedError", "message": "Authorization failed"}]},
                        status_code=401)


def _out(f, post: dict) -> dict:
    body = {"posts": [post]}
    return state.rename(body, "id", "post_id") if f and f.kind == "rename_field" else body


@router.get("/{inst:path}/ghost/api/admin/posts/slug/{slug}/")
async def by_slug(inst: str, slug: str, request: Request):
    if not _auth_ok(request):
        return _unauth()
    f = state.take_fault(P, inst, "find_existing")
    if f and (r := state.error_fault_response(f, "ghost")):
        return r
    await state.apply_delay(f)
    for post in state.instance(P, inst).posts.values():
        if post["slug"] == slug:
            return _out(f, post)
    return JSONResponse({"errors": [{"type": "NotFoundError", "message": "Post not found."}]}, status_code=404)


@router.post("/{inst:path}/ghost/api/admin/posts/")
async def create(inst: str, request: Request):
    if not _auth_ok(request):
        return _unauth()
    f = state.take_fault(P, inst, "publish")
    if f and (r := state.error_fault_response(f, "ghost")):
        return r
    data = (await request.json())["posts"][0]
    if len(data.get("title", "")) > 255:
        return JSONResponse({"errors": [{"type": "ValidationError", "message": "Validation error, cannot save post.",
                                         "context": "Value in [posts.title] exceeds maximum length of 255 characters."}]},
                            status_code=422)
    db = state.instance(P, inst)
    if any(p["slug"] == data.get("slug") for p in db.posts.values()):
        data["slug"] = f"{data['slug']}-2"  # real Ghost de-duplicates slugs, which is why we look up first
    pid = f"{state.next_id():024x}"
    base = str(request.base_url).rstrip("/")
    post = {"id": pid, "uuid": pid, "title": data.get("title"), "slug": data.get("slug"), "html": data.get("html"),
            "status": data.get("status", "draft"), "canonical_url": data.get("canonical_url"),
            "feature_image": data.get("feature_image"), "tags": data.get("tags", []),
            "url": f"{base}/{inst}/{data.get('slug')}/",
            "created_at": datetime.now(timezone.utc).isoformat()}
    db.posts[pid] = post
    await state.apply_delay(f)  # timeout fault: post is created, response arrives too late
    return JSONResponse(_out(f, post), status_code=201)


@router.get("/{inst:path}/ghost/api/admin/posts/{pid}/")
async def get_post(inst: str, pid: str, request: Request):
    if not _auth_ok(request):
        return _unauth()
    f = state.take_fault(P, inst, "get_status")
    if f and (r := state.error_fault_response(f, "ghost")):
        return r
    await state.apply_delay(f)
    post = state.instance(P, inst).posts.get(pid)
    if not post:
        return JSONResponse({"errors": [{"type": "NotFoundError", "message": "Post not found."}]}, status_code=404)
    return _out(f, post)


@router.delete("/{inst:path}/ghost/api/admin/posts/{pid}/")
async def delete_post(inst: str, pid: str, request: Request):
    if not _auth_ok(request):
        return _unauth()
    f = state.take_fault(P, inst, "delete")
    if f and (r := state.error_fault_response(f, "ghost")):
        return r
    await state.apply_delay(f)
    if state.instance(P, inst).posts.pop(pid, None) is None:
        return JSONResponse({"errors": [{"type": "NotFoundError", "message": "Post not found."}]}, status_code=404)
    return Response(status_code=204)


@router.get("/{inst:path}/ghost/api/admin/site/")
async def site(inst: str):
    return {"site": {"title": f"Mock Ghost {inst}", "version": "5.0"}}


def live_page(inst: str, slug: str):
    for post in state.instance(P, inst).posts.values():
        if post["slug"] == slug and post["status"] == "published":
            return HTMLResponse(f"<html><body><h1>{post['title']}</h1>{post['html'] or ''}</body></html>")
    return None
