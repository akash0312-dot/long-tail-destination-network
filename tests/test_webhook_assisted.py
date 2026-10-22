"""Signed webhook (partner sites) and assisted packs (a person posts)."""
import uuid

from app.connectors.webhook.connector import signature

PARTNER = {"params": {"endpoint_url": "https://partner-b.example/api/posts"}}
GOOD = {"credentials": {"signing_secret": "partner-test-secret"}}
RENDITION = {"title": "Five ways small studios reuse long videos", "body": "A practical breakdown.",
             "content_type": "article"}


def _publish(client, connection, key="wh-1"):
    return client.post("/connector/v1/webhook/publish", json={"rendition": RENDITION, "destination": PARTNER,
                                                              "connection": connection, "idempotency_key": key})


def test_signature_format_matches_protocol_doc():
    assert signature("s", "1700000000", b"{}") == \
        "sha256=" + __import__("hmac").new(b"s", b"1700000000.{}", "sha256").hexdigest()


def test_wrong_secret_is_rejected_by_partner(client, mock):
    r = _publish(client, {"credentials": {"signing_secret": "not-the-secret"}})
    err = r.json()["error"]
    assert r.status_code == 401 and err["code"] == "CONNECTION_AUTH_EXPIRED"
    assert err["details"]["platform_message"] == "invalid_signature"


def test_partner_revokes_permission_pauses_connection(client, mock):
    """Hard case 6b end to end: 403 from the partner -> connection marked expired -> later posts refused."""
    conn = client.post("/api/v1/connections", headers={"Idempotency-Key": uuid.uuid4().hex},
                       json={"connector_key": "webhook", "name": "partner",
                             "secret_refs": {"signing_secret": "env:MOCK_WEBHOOK_SECRET"}}).json()
    dest = client.post("/api/v1/destinations", headers={"Idempotency-Key": uuid.uuid4().hex},
                       json={"name": "Partner C", "url": "https://partner-c.example/api/posts",
                             "connector_key": "webhook", "connection_id": conn["id"]}).json()
    mock.arm(platform="webhook", op="publish", kind="auth_expired")
    job = client.post("/api/v1/publications", headers={"Idempotency-Key": uuid.uuid4().hex},
                      json={"destination_id": dest["id"], "rendition": RENDITION}).json()
    assert job["error"]["code"] == "CONNECTION_AUTH_EXPIRED"
    assert client.get("/api/v1/connections").json()["items"][-1]["status"] == "expired"
    again = client.post("/api/v1/publications", headers={"Idempotency-Key": uuid.uuid4().hex},
                        json={"destination_id": dest["id"], "rendition": RENDITION}).json()
    assert again["error"]["code"] == "CONNECTION_AUTH_EXPIRED" and "reconnect" in again["error"]["message"]


ASSIST_DEST = {"id": "hn-like", "url": "https://news.example/submit",
               "params": {"submit_url_template": "https://news.example/submit?url={url}&title={title}"},
               "rules_url": "https://news.example/guidelines", "self_promo_allowed": False,
               "frequency_cap": {"posts": 1, "per_days": 7}, "disclosure_required": True, "language": "en"}


def test_assist_pack_has_caption_deep_link_checklist_and_files(client):
    r = client.post("/connector/v1/assisted/build_assist_pack", json={
        "rendition": {**RENDITION, "link_url": "https://example.org/guide", "ai_generated": True,
                      "tags": ["video"], "media": [{"url": "https://cdn.example/cover.png", "type": "image"}]},
        "destination": ASSIST_DEST})
    assert r.status_code == 200, r.json()
    pack = r.json()
    assert pack["deep_link"].startswith("https://news.example/submit?url=https%3A%2F%2Fexample.org%2Fguide&title=Five")
    assert "#video" in pack["caption"] and "created with the help of AI" in pack["caption"]
    joined = " ".join(pack["checklist"])
    assert "guidelines" in joined and "1 time(s) per 7 day(s)" in joined and "Self-promotion is banned" in joined
    names = {f["name"] for f in pack["files"]}
    assert {"post.md", "pack.json", "media_1"} <= names
    md = next(f for f in pack["files"] if f["name"] == "post.md")
    assert client.get(md["url"].replace("http://testserver", "")).status_code == 200


def test_assist_pack_still_enforces_rules(client):
    r = client.post("/connector/v1/assisted/build_assist_pack",
                    json={"rendition": {**RENDITION, "is_promotional": True}, "destination": ASSIST_DEST})
    assert r.json()["error"]["code"] == "DEST_RULE_SELF_PROMO"


def test_destination_without_automation_gets_assist_pack_not_a_post(client, mock):
    url = f"https://lemmy-{uuid.uuid4().hex[:6]}.example"
    dest = client.post("/api/v1/destinations", headers={"Idempotency-Key": uuid.uuid4().hex},
                       json={"name": "manual-only community", "url": f"{url}/c/x", "connector_key": "lemmy",
                             "params": {"base_url": url, "community": "x"}, "automation_allowed": False}).json()
    job = client.post("/api/v1/publications", headers={"Idempotency-Key": uuid.uuid4().hex},
                      json={"destination_id": dest["id"], "rendition": RENDITION}).json()
    assert job["status"] == "succeeded", job
    res = job["result"]
    assert res["status"] == "awaiting_human" and res["assist_pack"]["checklist"]
    assert any(w.startswith("ASSIST_PACK") for w in res["warnings"])
    inst = "sim/lemmy/" + url.split("//")[1].replace(".", "-")
    assert mock.posts("lemmy", inst) == [], "nothing may be posted automatically"
    pack = client.get(res["links"]["pack.json"].replace("http://testserver", ""))
    assert pack.status_code == 200 and pack.json()["checklist"] == res["assist_pack"]["checklist"]
