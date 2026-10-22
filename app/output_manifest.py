"""Integration Standard point 9: every result carries a JSON manifest."""
from datetime import datetime, timezone

from app import __version__
from app.config import get_settings


def build_manifest(*, job_id: str | None, kind: str, connectors: list[dict] | None = None,
                   providers: list[dict] | None = None, timings_ms: dict | None = None,
                   warnings: list[str] | None = None, cost_estimate_usd: float = 0.0,
                   ai_generated: bool = False, seed: int | None = None, extra_settings: dict | None = None) -> dict:
    s = get_settings()
    return {
        "manifest_version": "1",
        "service": {"name": s.service_name, "version": __version__},
        "job_id": job_id,
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "connectors": connectors or [],
        "providers": providers or [],  # AI models/services used, with versions
        "settings": {"simulation_mode": s.simulation_mode, "environment": s.environment, **(extra_settings or {})},
        "seed": seed,
        "timings_ms": timings_ms or {},
        "cost_estimate_usd": round(cost_estimate_usd, 6),
        "warnings": warnings or [],
        "rights": {
            "licence": "Content supplied by the requesting creator; service output is commercially usable.",
            "ai_generated": ai_generated,
        },
    }
