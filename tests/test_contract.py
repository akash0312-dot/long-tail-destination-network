"""Connector contract shape: manifests, methods, error envelope, kill switch."""
from app.config import get_settings
from app.connectors.registry import all_connectors
from app.errors import ERROR_SPEC, ErrorCode

SHARED_CODES = {"DEST_SPEC_VIOLATION", "DEST_RULE_FREQUENCY_CAP", "CONNECTION_AUTH_EXPIRED", "PLATFORM_RATE_LIMIT",
                "PLATFORM_QUOTA_EXHAUSTED", "PLATFORM_CONTENT_REJECTED", "PLATFORM_UNAVAILABLE", "CONNECTOR_DISABLED"}


def test_every_shared_code_exists_with_retry_semantics():
    assert SHARED_CODES <= {c.value for c in ErrorCode}
    assert ERROR_SPEC[ErrorCode.PLATFORM_RATE_LIMIT][1] is True
    assert ERROR_SPEC[ErrorCode.PLATFORM_UNAVAILABLE][1] is True
    assert ERROR_SPEC[ErrorCode.DEST_SPEC_VIOLATION][1] is False
    assert ERROR_SPEC[ErrorCode.PLATFORM_CONTENT_REJECTED][1] is False
    assert ERROR_SPEC[ErrorCode.CONNECTOR_DISABLED][1] is False


def test_manifests_load_and_use_only_shared_codes():
    conns = all_connectors()
    assert {"ghost", "lemmy", "discourse", "devto", "webhook", "assisted"} <= set(conns)
    for key, c in conns.items():
        m = c.manifest
        assert m.key == key and m.instance_param
        assert "per_connection" in m.rate_limits
        required = {"assist"} if m.tier == "assisted" else {"publish", "status", "delete"}
        assert required <= set(m.capabilities)
        assert set(m.error_map.values()) <= {ErrorCode(x) for x in SHARED_CODES}


def test_manifest_endpoint(client):
    r = client.get("/connector/v1/ghost/manifest")
    assert r.status_code == 200 and r.json()["key"] == "ghost"


def test_unknown_connector_uses_error_envelope(client):
    r = client.post("/connector/v1/nope/publish", json={})
    body = r.json()
    assert r.status_code == 404
    assert set(body["error"]) == {"code", "message", "retryable", "details"}
    assert body["error"]["code"] == "CONNECTOR_NOT_FOUND"


def test_unknown_method(client):
    r = client.post("/connector/v1/ghost/explode", json={})
    assert r.json()["error"]["code"] == "METHOD_NOT_SUPPORTED"


def test_bad_body_is_validation_error(client):
    r = client.post("/connector/v1/ghost/publish", json={"rendition": {}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_optional_method_not_supported(client):
    r = client.post("/connector/v1/ghost/fetch_metrics",
                    json={"platform_post_id": "1", "connection": {}, "since": None})
    assert r.json()["error"]["code"] == "METHOD_NOT_SUPPORTED"


def test_kill_switch(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "disabled_connectors", "ghost")
    r = client.post("/connector/v1/ghost/validate",
                    json={"rendition": {"title": "t"}, "destination": {"params": {"base_url": "https://x.y"}}})
    assert r.status_code == 503
    assert r.json()["error"] == {"code": "CONNECTOR_DISABLED", "message": "Connector 'ghost' is disabled by the kill switch",
                                 "retryable": False, "details": {"connector": "ghost"}}
