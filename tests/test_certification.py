"""Certification harness: pass per connector, demotion on failure (hard case 5), cleanup, assisted tier, reconnect."""
import uuid

import pytest

RULES = {"frequency_cap": {"posts": 2, "per_days": 1}, "link_policy": {"allowed": True, "max_links": 2},
         "rules_url": "https://example.org/rules"}
SETUP = {
    "ghost": ({"admin_api_key": "env:NOPE_GHOST"}, {}, {}),
    "lemmy": ({"username": "env:NOPE_U", "password": "env:NOPE_P"}, {}, {"community": "certs"}),
    "discourse": ({"api_key": "env:NOPE_K"}, {"api_username": "cert_bot"}, {"category_id": 3}),
    "devto": ({"api_key": "env:NOPE_K"}, {}, {}),
    "webhook": ({"signing_secret": "env:MOCK_WEBHOOK_SECRET"}, {}, {}),
    "assisted": ({}, {}, {"submit_url": "https://news.example/submit"}),
    "hashnode": ({"api_key": "env:NOPE_K"}, {}, {"publication_id": "pub-1"}),
    "peertube": ({"username": "env:NOPE_U", "password": "env:NOPE_P"}, {}, {"channel_id": 3}),
}


def _post(client, path, body):
    return client.post(path, json=body, headers={"Idempotency-Key": uuid.uuid4().hex})


def make_dest(client, key, rules=RULES):
    refs, conn_params, params = SETUP[key]
    conn = _post(client, "/api/v1/connections", {"connector_key": key, "name": f"{key} bot",
                                                 "secret_refs": refs, "params": conn_params}).json()
    url = f"https://{key}-{uuid.uuid4().hex[:6]}.example"
    body = {"name": f"{key} cert target", "url": url, "connector_key": key, "params": params,
            "connection_id": conn["id"], **rules}
    if key == "lemmy":
        body["params"] = {**params, "base_url": url}
    r = _post(client, "/api/v1/destinations", body)
    assert r.status_code == 201, r.json()
    return r.json(), conn


def certify(client, dest_id):
    r = _post(client, "/api/v1/certifications", {"destination_id": dest_id})
    assert r.status_code == 202
    return client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()


@pytest.mark.parametrize("key,level", [("ghost", "L3"), ("lemmy", "L4"), ("discourse", "L4"), ("devto", "L4"),
                                       ("webhook", "L3"), ("assisted", "L1"), ("hashnode", "L3"),
                                       ("peertube", "L4")])  # L4 = native + metrics verified
def test_certification_passes_and_promotes(client, mock, key, level):
    d, _ = make_dest(client, key)
    job = certify(client, d["id"])
    assert job["status"] == "succeeded", job["error"]
    res = job["result"]
    assert res["passed"] and res["level"] == level
    assert all(s["ok"] for s in res["steps"]), res["steps"]
    if key != "assisted":
        names = [s["step"] for s in res["steps"]]
        expected = ["confirm_live_url", "status_published", *(["fetch_metrics"] if level == "L4" else []),
                    "delete_test_post", "status_deleted", "cleaned_within_limit"]
        assert names[-len(expected):] == expected
        assert client.get(res["links"]["live_snapshot"].replace("http://testserver", "")).status_code == 200
    evidence = client.get(res["links"]["evidence"].replace("http://testserver", "")).json()
    assert evidence["passed"] and evidence["manifest"]["kind"] == "certification"
    dest = client.get(f"/api/v1/destinations/{d['id']}").json()
    assert dest["level"] == level and dest["certification_status"] == "certified" and dest["last_certified_at"]
    runs = client.get("/api/v1/certifications", params={"destination_id": d["id"]}).json()["items"]
    assert len(runs) == 1 and runs[0]["passed"]


def test_renamed_field_fails_certification_demotes_alerts_and_cleans_up(client, mock):
    """Hard case 5: the platform renames a field; certification must fail and the destination must be demoted."""
    d, _ = make_dest(client, "ghost")
    assert certify(client, d["id"])["status"] == "succeeded"
    mock.arm(platform="ghost", op="get_status", kind="rename_field")
    job = certify(client, d["id"])
    assert job["status"] == "failed"
    err = job["error"]
    assert err["code"] == "CERTIFICATION_FAILED" and "status_published" in err["message"]
    steps = {s["step"]: s for s in err["details"]["steps"]}
    assert steps["status_published"]["error"]["code"] == "PLATFORM_SCHEMA_CHANGED"
    assert steps["cleanup"] == {"step": "cleanup", "ok": True,
                                "result": {"deleted_post_id": steps["publish_test_post"]["result"]["platform_post_id"]}}
    alert = client.get(err["details"]["links"]["alert"].replace("http://testserver", "")).json()
    assert alert["type"] == "destination_demoted"
    dest = client.get(f"/api/v1/destinations/{d['id']}").json()
    assert dest["level"] == "L0" and dest["certification_status"] == "failed"
    inst = "sim/ghost/" + d["url"].split("//")[1].replace(".", "-")
    assert mock.posts("ghost", inst) == [], "the test post must not be left behind"
    pub = _post(client, "/api/v1/publications", {"destination_id": d["id"], "rendition": {"title": "Hello there",
                                                                                           "body": "x"}}).json()
    assert pub["error"]["code"] == "DEST_NOT_CERTIFIED", "a demoted destination is excluded"


def test_lost_publish_response_is_still_cleaned_up(client, mock):
    """The post is created but its id never reaches us; cleanup finds it through reconcile and deletes it."""
    d, _ = make_dest(client, "lemmy")
    mock.arm(platform="lemmy", op="publish", kind="rename_field")
    job = certify(client, d["id"])
    assert job["status"] == "failed"
    assert job["error"]["details"]["steps"][-1]["step"] == "cleanup"
    assert job["error"]["details"]["steps"][-1]["ok"] is True
    inst = "sim/lemmy/" + d["url"].split("//")[1].replace(".", "-")
    assert all(p["deleted"] for p in mock.posts("lemmy", inst))


def test_missing_rules_fail_certification(client, mock):
    d, _ = make_dest(client, "devto", rules={})
    job = certify(client, d["id"])
    assert job["status"] == "failed" and "rules_recorded" in job["error"]["message"]
    assert [s["step"] for s in job["error"]["details"]["steps"]] == ["rules_recorded"], "nothing may be posted"
    assert mock.calls() == 0


def test_reconnect_verifies_and_reactivates(client, mock):
    d, conn = make_dest(client, "lemmy")
    client.patch(f"/api/v1/connections/{conn['id']}", json={"status": "expired"})
    r = _post(client, f"/api/v1/connections/{conn['id']}/reconnect", {})
    assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["verified"] is True


def test_reconnect_fails_when_credentials_still_bad(client, mock):
    d, conn = make_dest(client, "lemmy")
    client.patch(f"/api/v1/connections/{conn['id']}", json={"status": "expired"})
    mock.arm(platform="lemmy", op="login", kind="auth_expired")
    r = _post(client, f"/api/v1/connections/{conn['id']}/reconnect", {})
    assert r.json()["error"]["code"] == "CONNECTION_AUTH_EXPIRED"
    assert [c for c in client.get("/api/v1/connections").json()["items"] if c["id"] == conn["id"]][0]["status"] \
        == "expired"
