"""In-memory platform state and fault injection shared by all mock platforms.

Faults are armed with POST /__admin/faults, e.g.
  {"platform": "ghost", "op": "publish", "kind": "timeout", "times": 1, "delay_s": 3}
kinds: timeout (do the work, then answer late) | rate_limit | auth_expired | reject | server_error | rename_field
"""
import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel


class FaultSpec(BaseModel):
    platform: str
    op: str = "*"
    kind: str
    instance: str | None = None
    times: int = 1
    delay_s: float = 3.0
    reset_s: float = 2.0
    message: str | None = None


@dataclass
class Instance:
    posts: dict[str, dict] = field(default_factory=dict)
    communities: dict[str, dict] = field(default_factory=dict)
    users: dict[str, dict] = field(default_factory=dict)


_ids = itertools.count(1)
instances: dict[tuple[str, str], Instance] = {}
faults: list[FaultSpec] = []
calls: list[dict] = []


def next_id() -> int:
    return next(_ids)


def instance(platform: str, name: str) -> Instance:
    return instances.setdefault((platform, name), Instance())


def reset() -> None:
    instances.clear()
    faults.clear()
    calls.clear()


def take_fault(platform: str, inst: str, op: str) -> FaultSpec | None:
    calls.append({"platform": platform, "instance": inst, "op": op, "t": time.time()})
    for f in faults:
        if f.platform == platform and f.op in ("*", op) and f.instance in (None, inst) and f.times > 0:
            f.times -= 1
            if f.times == 0:
                faults.remove(f)
            return f
    return None


def error_fault_response(f: FaultSpec, style: str) -> JSONResponse | None:
    """Faults that replace the response entirely. style = 'ghost' | 'lemmy' | 'json'."""
    if f.kind == "rate_limit":
        headers = {"Retry-After": str(int(f.reset_s) if f.reset_s >= 1 else f.reset_s),
                   "X-RateLimit-Reset": str(int(time.time() + f.reset_s))}
        body = ({"errors": [{"type": "TooManyRequestsError", "message": "Too many requests"}]} if style == "ghost"
                else {"error": "rate_limit_error"})
        return JSONResponse(body, status_code=429, headers=headers)
    if f.kind == "auth_expired":
        body = ({"errors": [{"type": "UnauthorizedError", "message": "Invalid token"}]} if style == "ghost"
                else {"error": "not_logged_in"})
        return JSONResponse(body, status_code=401 if style == "ghost" else 400)
    if f.kind == "reject":
        msg = f.message or "Content rejected by moderation"
        body = ({"errors": [{"type": "ValidationError", "message": msg}]} if style == "ghost"
                else {"error": f.message or "blocked_url"})
        return JSONResponse(body, status_code=422 if style == "ghost" else 400)
    if f.kind == "server_error":
        return JSONResponse({"error": "service unavailable"}, status_code=503)
    return None


async def apply_delay(f: FaultSpec | None) -> None:
    if f and f.kind == "timeout":
        await asyncio.sleep(f.delay_s)


def rename(obj: Any, old: str, new: str) -> Any:
    """Recursively rename a key: simulates a platform renaming a field in its API."""
    if isinstance(obj, dict):
        return {(new if k == old else k): rename(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rename(x, old, new) for x in obj]
    return obj
