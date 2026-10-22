"""Integration Standard 2: openapi.yaml (OpenAPI 3.1) matches the running service exactly."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_openapi_file_matches_running_service(client):
    on_disk = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    live = client.get("/openapi.json").json()
    assert on_disk == live, "openapi.yaml is stale: run  python scripts/export_openapi.py"


def test_docs_has_bearer_authorize_on_protected_routes_only(client):
    spec = client.get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http", "scheme": "bearer", "description": spec["components"]["securitySchemes"]["bearerAuth"]
        ["description"]}
    for path, ops in spec["paths"].items():
        protected = path.startswith(("/api/v1/", "/connector/v1")) and not path.startswith("/api/v1/feeds/")
        for op in ops.values():
            assert (op.get("security") == [{"bearerAuth": []}]) is protected, path
    page = client.get("/docs")
    assert page.status_code == 200 and "persistAuthorization" in page.text


def test_token_entered_in_docs_is_what_the_api_checks(client, monkeypatch):
    """The scheme the docs declare (Authorization: Bearer <token>) is exactly what the middleware accepts."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "api_token", "demo-token-123456")
    assert client.get("/api/v1/connectors").status_code == 401
    assert client.get("/api/v1/connectors", headers={"Authorization": "Bearer demo-token-123456"}).status_code == 200
    assert client.get("/docs").status_code == 200 and client.get("/openapi.json").status_code == 200


def test_openapi_version_and_route_prefixes(client):
    spec = client.get("/openapi.json").json()
    assert spec["openapi"].startswith("3.1")
    allowed = ("/api/v1/", "/connector/v1", "/health", "/ready")  # contract + health paths are fixed by the brief
    assert all(path.startswith(allowed) for path in spec["paths"]), sorted(spec["paths"])
