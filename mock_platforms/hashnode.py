"""Mock of the Hashnode GraphQL API — the four operations the connector sends. Instance = URL prefix."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from mock_platforms import state

router = APIRouter()
P = "hashnode"


def _gql_error(code: str, message: str):
    return JSONResponse({"errors": [{"message": message, "extensions": {"code": code}}], "data": None})


def _fault(f):
    if f is None:
        return None
    if f.kind == "auth_expired":
        return _gql_error("UNAUTHENTICATED", "Invalid access token")
    if f.kind == "reject":
        return _gql_error("BAD_USER_INPUT", f.message or "Post content violates guidelines")
    return state.error_fault_response(f, "json")


def _op(query: str) -> str:
    for name in ("publishPost", "removePost", "publication", "post"):
        if f"{name}(" in query:
            return name
    return "unknown"


@router.post("/{inst:path}/")
async def graphql(inst: str, request: Request):
    if not inst.startswith(("sim/hashnode", "local/hashnode")):
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await request.json()
    op = _op(body.get("query", ""))
    fault_op = {"publishPost": "publish", "removePost": "delete", "publication": "find_existing",
                "post": "get_status"}.get(op, op)
    if not request.headers.get("authorization"):
        return _gql_error("UNAUTHENTICATED", "Missing access token")
    f = state.take_fault(P, inst, fault_op)
    if f and (r := _fault(f)):
        return r
    db = state.instance(P, inst)
    v = body.get("variables", {})
    base = str(request.base_url).rstrip("/")
    if op == "publication":
        posts = sorted((p for p in db.posts.values() if not p["removed"]), key=lambda p: p["n"], reverse=True)
        return {"data": {"publication": {"posts": {"edges": [
            {"node": {"id": p["id"], "url": p["url"], "content": {"markdown": p["markdown"]}}} for p in posts[:20]]}}}}
    if op == "publishPost":
        inp = v["input"]
        if len(inp.get("title", "")) > 250:
            return _gql_error("BAD_USER_INPUT", "Title is too long")
        n = state.next_id()
        pid = f"{n:024x}"
        db.posts[pid] = {"id": pid, "n": n, "url": f"{base}/{inst}/__live/hashnode/{pid}", "title": inp["title"],
                         "markdown": inp.get("contentMarkdown", ""), "removed": False}
        await state.apply_delay(f)
        post = {"id": pid, "url": db.posts[pid]["url"]}
        if f and f.kind == "rename_field":
            post = state.rename(post, "id", "postId")
        return {"data": {"publishPost": {"post": post}}}
    if op == "post":
        await state.apply_delay(f)
        p = db.posts.get(v.get("id"))
        out = None if not p or p["removed"] else {"id": p["id"], "url": p["url"]}
        if out and f and f.kind == "rename_field":
            out = state.rename(out, "url", "link")
        return {"data": {"post": out}}
    if op == "removePost":
        p = db.posts.get(v["input"]["id"])
        if not p:
            return _gql_error("NOT_FOUND", "Post not found")
        p["removed"] = True
        return {"data": {"removePost": {"post": {"id": p["id"]}}}}
    return _gql_error("GRAPHQL_VALIDATION_FAILED", "Unknown operation")


@router.get("/{inst:path}/__live/hashnode/{pid}")
async def live(inst: str, pid: str):
    p = state.instance(P, inst).posts.get(pid)
    if p and not p["removed"]:
        return HTMLResponse(f"<html><body><h1>{p['title']}</h1></body></html>")
    return HTMLResponse("not found", status_code=404)
