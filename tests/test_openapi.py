"""Integration Standard 2: openapi.yaml (OpenAPI 3.1) matches the running service exactly."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_openapi_file_matches_running_service(client):
    on_disk = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    live = client.get("/openapi.json").json()
    assert on_disk == live, "openapi.yaml is stale: run  python scripts/export_openapi.py"


def test_openapi_version_and_route_prefixes(client):
    spec = client.get("/openapi.json").json()
    assert spec["openapi"].startswith("3.1")
    allowed = ("/api/v1/", "/connector/v1", "/health", "/ready")  # contract + health paths are fixed by the brief
    assert all(path.startswith(allowed) for path in spec["paths"]), sorted(spec["paths"])
