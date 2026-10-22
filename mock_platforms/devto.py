"""Mock of the Forem (Dev.to) API v1 — the subset the connector uses. Instance = URL prefix."""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from mock_platforms import state

router = APIRouter()
P = "devto"


def _err(msg: str, status: int):
    return JSONResponse({"error": msg, "status": status}, status_code=status)


def _fault(f):
    if f is None:
        return None
    if f.kind == "auth_expired":
        return _err("unauthorized", 401)
    if f.kind == "reject":
        return _err(f.message or "Body markdown has been blocked as spam", 422)
    return state.error_fault_response(f, "json")


def _view(a: dict, f=None) -> dict:
    body = {k: a[k] for k in ("id", "title", "url", "published", "body_markdown", "tags", "slug")}
    return state.rename(body, "id", "article_id") if f and f.kind == "rename_field" else body


@router.get("/{inst:path}/api/articles/me/all")
async def my_articles(inst: str, request: Request):
    if not request.headers.get("api-key"):
        return _err("unauthorized", 401)
    f = state.take_fault(P, inst, "find_existing")
    if f and (r := _fault(f)):
        return r
    arts = sorted(state.instance(P, inst).posts.values(), key=lambda a: a["id"], reverse=True)
    return [_view(a) for a in arts]


@router.post("/{inst:path}/api/articles")
async def create(inst: str, request: Request):
    if not request.headers.get("api-key"):
        return _err("unauthorized", 401)
    f = state.take_fault(P, inst, "publish")
    if f and (r := _fault(f)):
        return r
    art = (await request.json()).get("article", {})
    if not art.get("title") or len(art["title"]) > 128:
        return _err("Title is too long (maximum is 128 characters)", 422)
    if len(art.get("tags", [])) > 4:
        return _err("Tag list exceed the maximum of 4 tags", 422)
    aid = state.next_id()
    slug = re.sub(r"[^a-z0-9]+", "-", art["title"].lower()).strip("-") + f"-{aid}"
    base = str(request.base_url).rstrip("/")
    a = {"id": aid, "title": art["title"], "slug": slug, "published": bool(art.get("published")),
         "body_markdown": art.get("body_markdown", ""), "tags": art.get("tags", []),
         "url": f"{base}/{inst}/__live/devto/{aid}", "created_at": datetime.now(timezone.utc).isoformat()}
    state.instance(P, inst).posts[aid] = a
    await state.apply_delay(f)
    return JSONResponse(_view(a, f), status_code=201)


@router.get("/{inst:path}/api/articles/{aid:int}")
async def get_article(inst: str, aid: int):
    f = state.take_fault(P, inst, "get_status")
    if f and (r := _fault(f)):
        return r
    await state.apply_delay(f)
    a = state.instance(P, inst).posts.get(aid)
    if not a or not a["published"]:
        return _err("not found", 404)
    return _view(a, f)


@router.put("/{inst:path}/api/articles/{aid:int}")
async def update_article(inst: str, aid: int, request: Request):
    if not request.headers.get("api-key"):
        return _err("unauthorized", 401)
    f = state.take_fault(P, inst, "delete")
    if f and (r := _fault(f)):
        return r
    a = state.instance(P, inst).posts.get(aid)
    if not a:
        return _err("not found", 404)
    a["published"] = bool((await request.json()).get("article", {}).get("published", a["published"]))
    return _view(a)


@router.get("/{inst:path}/__live/devto/{aid:int}")
async def live(inst: str, aid: int):
    a = state.instance(P, inst).posts.get(aid)
    if a and a["published"]:
        return HTMLResponse(f"<html><body><h1>{a['title']}</h1></body></html>")
    return HTMLResponse("not found", status_code=404)
