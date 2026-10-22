"""Mock of the Discourse API — the subset the connector uses. Instance = URL prefix."""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from mock_platforms import state

router = APIRouter()
P = "discourse"


def _forbidden():
    return JSONResponse({"errors": ["You are not permitted to view the requested resource. The API username or "
                                    "key is invalid."], "error_type": "invalid_access"}, status_code=403)


def _fault(f):
    if f is None:
        return None
    if f.kind == "auth_expired":
        return _forbidden()
    if f.kind == "reject":
        return JSONResponse({"errors": [f.message or "Body seems unclear, is it a complete sentence?"],
                             "error_type": "invalid_parameters"}, status_code=422)
    if f.kind == "rate_limit":
        return JSONResponse({"errors": ["You've performed this action too many times."], "error_type": "rate_limit",
                             "extras": {"wait_seconds": f.reset_s}}, status_code=429,
                            headers={"Retry-After": str(int(f.reset_s) if f.reset_s >= 1 else f.reset_s)})
    return state.error_fault_response(f, "json")


def _authed(request: Request) -> bool:
    return bool(request.headers.get("api-key")) and bool(request.headers.get("api-username"))


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "topic"


@router.get("/{inst:path}/about.json")
async def about(inst: str):
    return {"about": {"title": f"Mock forum {inst}", "stats": {"users_count": 2500, "topics_count": 900}}}


@router.post("/{inst:path}/posts.json")
async def create(inst: str, request: Request):
    if not _authed(request):
        return _forbidden()
    f = state.take_fault(P, inst, "publish")
    if f and (r := _fault(f)):
        return r
    data = await request.json()
    db = state.instance(P, inst)
    title = data.get("title", "")
    if any(t["title"].lower() == title.lower() and not t["deleted_at"] for t in db.posts.values()):
        return JSONResponse({"errors": ["Title has already been used"], "error_type": "invalid_parameters"},
                            status_code=422)
    if len(title) > 255 or len(title) < 15:
        return JSONResponse({"errors": ["Title is invalid"]}, status_code=422)
    tid, pid = state.next_id(), state.next_id()
    topic = {"id": tid, "title": title, "slug": _slug(title), "visible": True, "deleted_at": None,
             "created_by": request.headers.get("api-username"), "first_post_id": pid, "raw": data.get("raw", ""),
             "category_id": data.get("category"), "created_at": datetime.now(timezone.utc).isoformat()}
    db.posts[tid] = topic
    await state.apply_delay(f)
    body = {"id": pid, "topic_id": tid, "topic_slug": topic["slug"], "post_number": 1, "raw": topic["raw"]}
    if f and f.kind == "rename_field":
        body = state.rename(body, "topic_id", "topicId")
    return body


@router.get("/{inst:path}/topics/created-by/{username}.json")
async def created_by(inst: str, username: str, request: Request):
    f = state.take_fault(P, inst, "find_existing")
    if f and (r := _fault(f)):
        return r
    topics = [t for t in state.instance(P, inst).posts.values()
              if t["created_by"] == username and not t["deleted_at"]]
    topics.sort(key=lambda t: t["id"], reverse=True)
    return {"topic_list": {"topics": [{"id": t["id"], "title": t["title"], "slug": t["slug"]} for t in topics]}}


@router.get("/{inst:path}/t/{tid:int}.json")
async def get_topic(inst: str, tid: int, request: Request):
    f = state.take_fault(P, inst, "get_status")
    if f and (r := _fault(f)):
        return r
    await state.apply_delay(f)
    t = state.instance(P, inst).posts.get(tid)
    if not t or t["deleted_at"]:
        return JSONResponse({"errors": ["The requested URL or resource could not be found."]}, status_code=404)
    body = {"id": t["id"], "title": t["title"], "slug": t["slug"], "visible": t["visible"], "deleted_at": None,
            "views": 120, "like_count": 9, "posts_count": 4,
            "details": {"links": [{"url": "https://example.org/a", "clicks": 7}, {"url": "https://example.org/b",
                                                                                 "clicks": 2}]},
            "post_stream": {"posts": [{"id": t["first_post_id"], "post_number": 1}]}}
    return state.rename(body, "slug", "topic_slug") if f and f.kind == "rename_field" else body


@router.get("/{inst:path}/posts/{pid:int}.json")
async def get_post(inst: str, pid: int):
    for t in state.instance(P, inst).posts.values():
        if t["first_post_id"] == pid:
            return {"id": pid, "topic_id": t["id"], "raw": t["raw"]}
    return JSONResponse({"errors": ["not found"]}, status_code=404)


@router.delete("/{inst:path}/t/{tid:int}.json")
async def delete_topic(inst: str, tid: int, request: Request):
    if not _authed(request):
        return _forbidden()
    f = state.take_fault(P, inst, "delete")
    if f and (r := _fault(f)):
        return r
    t = state.instance(P, inst).posts.get(tid)
    if not t or t["deleted_at"]:
        return JSONResponse({"errors": ["not found"]}, status_code=404)
    t["deleted_at"] = datetime.now(timezone.utc).isoformat()
    return {"success": "OK"}


@router.get("/{inst:path}/t/{slug}/{tid:int}")
async def live(inst: str, slug: str, tid: int):
    t = state.instance(P, inst).posts.get(tid)
    if t and not t["deleted_at"]:
        return HTMLResponse(f"<html><body><h1>{t['title']}</h1><div>{t['raw']}</div></body></html>")
    return HTMLResponse("not found", status_code=404)
