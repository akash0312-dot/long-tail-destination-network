"""End-to-end against the running compose stack with REAL local Ghost and Lemmy (SIMULATION_MODE=false).

    docker compose up -d && RUN_E2E=1 E2E_API_URL=http://localhost:8000 pytest tests/test_e2e_local.py

Publishes through /api/v1 (job + worker), checks the live URL from inside the network via get_status, deletes the
post (well within the 10-minute rule) and confirms it is gone.
"""
import os
import time
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="needs the compose stack (RUN_E2E=1)")]
API = os.environ.get("E2E_API_URL", "http://localhost:8000")
HEADERS = {"Authorization": f"Bearer {os.environ['API_TOKEN']}"} if os.environ.get("API_TOKEN") else {}


def _api(method, path, **kw):
    headers = {**HEADERS, **kw.pop("headers", {})}
    return httpx.request(method, f"{API}{path}", headers=headers, timeout=60, **kw)


def _wait_job(job_id: str, timeout_s: float = 60) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = _api("GET", f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.5)
    raise AssertionError(f"job {job_id} did not finish")


def _local_destination(connector_key: str) -> dict:
    items = _api("GET", "/api/v1/destinations", params={"connector_key": connector_key}).json()["items"]
    local = [d for d in items if "(local" in d["name"]]
    assert local, f"bootstrap did not seed a local {connector_key} destination"
    return local[0]


def _connection(dest: dict) -> dict:
    conns = {c["id"]: c for c in _api("GET", "/api/v1/connections").json()["items"]}
    c = conns[dest["connection_id"]]
    refs = {"ghost": {"admin_api_key": "file:/run/app-secrets/ghost_admin_api_key"},
            "lemmy": {"password": "env:LEMMY_ADMIN_PASSWORD"}}[dest["connector_key"]]
    return {"id": c["id"], "secret_refs": refs, "params": c["params"]}


@pytest.mark.parametrize("connector_key", ["ghost", "lemmy"])
def test_publish_status_delete_on_real_local_instance(connector_key):
    dest = _local_destination(connector_key)
    rendition = {"title": f"[certification test] {uuid.uuid4().hex[:8]}",
                 "body": "Automated connector test post. It is deleted within seconds.", "content_type": "text"}
    key = f"e2e-{uuid.uuid4().hex}"
    started = time.time()

    r = _api("POST", "/api/v1/publications", json={"destination_id": dest["id"], "rendition": rendition,
                                                   "is_test": True}, headers={"Idempotency-Key": key})
    assert r.status_code == 202, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "succeeded", job
    res = job["result"]
    assert res["status"] == "published" and res["platform_post_id"]

    # same Idempotency-Key -> same job, no second post
    again = _api("POST", "/api/v1/publications", json={"destination_id": dest["id"], "rendition": rendition,
                                                       "is_test": True}, headers={"Idempotency-Key": key})
    assert again.json()["job_id"] == r.json()["job_id"]

    ref = {"platform_post_id": res["platform_post_id"], "connection": _connection(dest),
           "destination": {"id": dest["id"], "params": dest["params"], "url": dest["url"]}}
    st = _api("POST", f"/connector/v1/{connector_key}/get_status", json=ref).json()
    assert st["status"] == "published", st

    d = _api("POST", f"/connector/v1/{connector_key}/delete", json=ref).json()
    assert d == {"deleted": True}
    st = _api("POST", f"/connector/v1/{connector_key}/get_status", json=ref).json()
    assert st["status"] == "deleted", st
    assert time.time() - started < 600, "test posts must be deleted within 10 minutes"
