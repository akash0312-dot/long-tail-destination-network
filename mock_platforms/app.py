"""Mock platform server for simulation mode and tests: uvicorn mock_platforms.app:app --port 9000"""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from mock_platforms import devto, discourse, ghost, lemmy, partner, state

app = FastAPI(title="Long-tail mock platforms", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/__admin/faults")
async def add_fault(spec: state.FaultSpec):
    state.faults.append(spec)
    return {"armed": spec.model_dump(), "active": len(state.faults)}


@app.delete("/__admin/faults")
async def clear_faults():
    state.faults.clear()
    return {"active": 0}


@app.post("/__admin/reset")
async def reset():
    state.reset()
    callbacks.clear()
    return {"reset": True}


@app.get("/__admin/state")
async def get_state():
    return {"instances": {f"{p}:{i}": {"posts": len(v.posts), "communities": len(v.communities)}
                          for (p, i), v in state.instances.items()},
            "faults": [f.model_dump() for f in state.faults], "calls": len(state.calls)}


@app.get("/__admin/posts")
async def posts(platform: str, instance: str):
    return {"posts": list(state.instance(platform, instance).posts.values())}


callbacks: list[dict] = []


@app.post("/__admin/callback-sink")
async def callback_sink(request: Request):
    callbacks.append({"headers": dict(request.headers), "body": (await request.body()).decode()})
    return {"received": len(callbacks)}


@app.get("/__admin/callback-sink")
async def callback_list():
    return {"callbacks": callbacks}


app.include_router(ghost.router)
app.include_router(lemmy.router)
app.include_router(discourse.router)
app.include_router(devto.router)
app.include_router(partner.router)


# public "live" pages, registered last so API routes win
@app.get("/{inst:path}/post/{pid:int}")
async def lemmy_live(inst: str, pid: int):
    return lemmy.live_page(inst, pid) or HTMLResponse("not found", status_code=404)


@app.get("/{inst:path}/{slug}/")
async def ghost_live(inst: str, slug: str):
    return ghost.live_page(inst, slug) or HTMLResponse("not found", status_code=404)
