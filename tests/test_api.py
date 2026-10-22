"""/api/v1: health, idempotency, catalogue rows, publication jobs, callbacks, revoked connections."""
import hashlib
import hmac
import json
import uuid

from app.config import get_settings

LEMMY_DEST = {"name": "Lemmy test community", "url": "https://lemmy-b.example/c/longtail_test",
              "connector_key": "lemmy", "params": {"base_url": "https://lemmy-b.example", "community": "longtail_test"},
              "audience_tags": ["ai", "creators"]}
RENDITION = {"title": "What tools do small video teams use?", "body": "Curious what works for you.",
             "content_type": "text"}


def post(client, path, body, key=None):
    return client.post(path, json=body, headers={"Idempotency-Key": key or uuid.uuid4().hex})


def make_destination(client, **overrides):
    conn = post(client, "/api/v1/connections",
                {"connector_key": "lemmy", "name": f"bot-{uuid.uuid4().hex[:6]}",
                 "secret_refs": {"username": "env:LEMMY_TEST_USER", "password": "env:LEMMY_TEST_PASSWORD"}})
    assert conn.status_code == 201, conn.json()
    body = {**LEMMY_DEST, "url": f"https://lemmy-{uuid.uuid4().hex[:6]}.example/c/longtail_test",
            "connection_id": conn.json()["id"], **overrides}
    body["params"] = {**LEMMY_DEST["params"], "base_url": body["url"].split("/c/")[0]}
    r = post(client, "/api/v1/destinations", body)
    assert r.status_code == 201, r.json()
    return r.json(), conn.json()


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"


def test_correlation_id_is_echoed(client):
    r = client.get("/health", headers={"X-Correlation-Id": "abc-123"})
    assert r.headers["X-Correlation-Id"] == "abc-123"


def test_post_without_idempotency_key_rejected(client):
    r = client.post("/api/v1/destinations", json=LEMMY_DEST)
    assert r.status_code == 400 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_inline_secret_rejected(client):
    r = post(client, "/api/v1/connections", {"connector_key": "lemmy", "name": "x",
                                             "secret_refs": {"password": "hunter2-plain"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_connection_view_hides_refs(client):
    _, conn = make_destination(client)
    assert all(v.endswith(":***") for v in conn["secret_refs"].values())


def test_duplicate_destination_detected_by_normalised_url(client):
    d, _ = make_destination(client)
    dup = {**LEMMY_DEST, "url": d["url"].upper().replace("HTTPS://", "https://www.") + "/?utm_source=x"}
    r = post(client, "/api/v1/destinations", dup)
    assert r.status_code == 400 and r.json()["error"]["details"]["existing_id"] == d["id"]


def test_publication_job_end_to_end(client, mock):
    d, _ = make_destination(client)
    r = post(client, "/api/v1/publications", {"destination_id": d["id"], "rendition": RENDITION})
    assert r.status_code == 202
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded" and job["progress"] == 100, job
    res = job["result"]
    assert res["status"] == "published" and res["live_url"]
    m = res["manifest"]
    assert {"providers", "settings", "seed", "timings_ms", "cost_estimate_usd", "warnings", "rights"} <= set(m)
    manifest_link = job["links"]["manifest"]
    assert client.get(manifest_link.replace("http://testserver", "")).status_code == 200
    pub = client.get(f"/api/v1/publications/{res['publication_id']}").json()
    assert pub["status"] == "published"


def test_same_idempotency_key_returns_same_job_and_one_post(client, mock):
    d, _ = make_destination(client)
    body = {"destination_id": d["id"], "rendition": RENDITION}
    r1 = post(client, "/api/v1/publications", body, key="idem-api-1")
    r2 = post(client, "/api/v1/publications", body, key="idem-api-1")
    assert r1.json()["job_id"] == r2.json()["job_id"]
    assert r2.headers.get("Idempotent-Replayed") == "true"
    inst = "sim/lemmy/" + d["params"]["base_url"].split("//")[1].replace(".", "-")
    assert len(mock.posts("lemmy", inst)) == 1


def test_same_key_different_body_conflicts(client):
    d, _ = make_destination(client)
    post(client, "/api/v1/publications", {"destination_id": d["id"], "rendition": RENDITION}, key="idem-api-2")
    r = post(client, "/api/v1/publications",
             {"destination_id": d["id"], "rendition": {**RENDITION, "title": "other"}}, key="idem-api-2")
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_revoked_connection_fails_with_reconnect_message(client, mock):
    """Hard case 6b: a partner revokes permission -> nothing is posted, owner must reconnect."""
    d, conn = make_destination(client)
    r = client.patch(f"/api/v1/connections/{conn['id']}", json={"status": "revoked"})
    assert r.status_code == 200
    job = post(client, "/api/v1/publications", {"destination_id": d["id"], "rendition": RENDITION}).json()
    assert job["status"] == "failed"
    assert job["error"]["code"] == "CONNECTION_AUTH_EXPIRED" and "reconnect" in job["error"]["message"]


def test_spec_violation_job_fails_with_field_reasons(client):
    d, _ = make_destination(client)
    job = post(client, "/api/v1/publications",
               {"destination_id": d["id"], "rendition": {**RENDITION, "title": "t" * 250}}).json()
    assert job["status"] == "failed" and job["error"]["code"] == "DEST_SPEC_VIOLATION"
    assert job["error"]["details"]["violations"][0]["code"] == "TITLE_TOO_LONG"


def test_callback_is_hmac_signed(client, mock):
    d, _ = make_destination(client)
    post(client, "/api/v1/publications", {"destination_id": d["id"], "rendition": RENDITION,
                                          "callback_url": f"{mock.url}/__admin/callback-sink"})
    cb = mock.callbacks()[-1]
    expected = "sha256=" + hmac.new(b"test-callback-secret", cb["body"].encode(), hashlib.sha256).hexdigest()
    header = get_settings().callback_signature_header.lower()
    assert cb["headers"][header] == expected
    assert json.loads(cb["body"])["status"] == "succeeded"


def test_job_not_found_envelope(client):
    r = client.get("/api/v1/jobs/doesnotexist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
