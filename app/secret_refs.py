"""Resolve secret references. Secrets never live in the DB or repo; only references like env:NAME or file:/path."""
import os
from pathlib import Path

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.logging_setup import register_secret


def resolve_ref(ref: str) -> str | None:
    if ref.startswith("env:"):
        return os.environ.get(ref[4:])
    if ref.startswith("file:"):
        p = Path(ref[5:])
        return p.read_text(encoding="utf-8").strip() if p.exists() else None
    raise AppError(ErrorCode.VALIDATION_ERROR, "Secret references must start with env: or file:", {"ref": ref[:10]})


def resolve_credentials(secret_refs: dict[str, str]) -> dict[str, str]:
    """Return {name: value}. In simulation mode a missing secret becomes a harmless dummy value."""
    sim = get_settings().simulation_mode
    out: dict[str, str] = {}
    for name, ref in (secret_refs or {}).items():
        value = resolve_ref(ref)
        if value is None:
            if sim:
                value = f"sim-{name}"
            else:
                raise AppError(ErrorCode.SECRET_UNAVAILABLE, f"Secret '{name}' could not be resolved",
                               {"name": name, "ref_kind": ref.split(":", 1)[0]})
        register_secret(value)
        out[name] = value
    return out
