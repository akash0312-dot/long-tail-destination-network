"""Outstanding: fetch_metrics on 4 connectors, normalised to the contract's fields (None = not exposed)."""
import uuid

import pytest

from app.connectors.base import METRIC_KEYS
from tests.test_connectors import TARGETS, base_rendition, call, post_ref, publish

EXPECTED = {
    "lemmy": {"likes": 1, "comments": 0, "views": None, "shares": None, "clicks": None},
    "discourse": {"views": 120, "likes": 9, "comments": 3, "clicks": 9, "shares": None},
    "devto": {"views": 340, "likes": 21, "comments": 5, "shares": None},
    "peertube": {"views": 512, "likes": 33, "comments": 6, "watch_time_s": 4096},
}


@pytest.mark.parametrize("key", EXPECTED)
def test_fetch_metrics_normalised(client, mock, key):
    status, res = publish(client, key)
    assert status == 200, res
    status, m = call(client, key, "fetch_metrics", **post_ref(key, res["platform_post_id"]), since=None)
    assert status == 200, m
    assert set(m) == set(METRIC_KEYS), "exactly the contract's fields"
    for field, value in EXPECTED[key].items():
        assert m[field] == value, (field, m)


@pytest.mark.parametrize("key", ["ghost", "webhook", "hashnode"])
def test_connectors_without_metrics_say_so(client, key):
    status, err = call(client, key, "fetch_metrics", **post_ref(key, "1"), since=None)
    assert err["error"]["code"] == "METHOD_NOT_SUPPORTED"


def test_metrics_schema_change_is_detected(client, mock):
    status, res = publish(client, "lemmy")
    mock.arm(platform="lemmy", op="get_status", kind="rename_field")
    status, err = call(client, "lemmy", "fetch_metrics", **post_ref("lemmy", res["platform_post_id"]), since=None)
    assert err["error"]["code"] == "PLATFORM_SCHEMA_CHANGED"


def test_publication_metrics_are_stored(client, mock):
    conn = client.post("/api/v1/connections", headers={"Idempotency-Key": uuid.uuid4().hex},
                       json={"connector_key": "devto", "name": "forem", "secret_refs": {"api_key": "env:NOPE"}}).json()
    url = f"https://forem-{uuid.uuid4().hex[:6]}.example"
    d = client.post("/api/v1/destinations", headers={"Idempotency-Key": uuid.uuid4().hex},
                    json={"name": url, "url": url, "connector_key": "devto", "connection_id": conn["id"],
                          "self_promo_allowed": True}).json()
    job = client.post("/api/v1/publications", headers={"Idempotency-Key": uuid.uuid4().hex},
                      json={"destination_id": d["id"], "rendition": base_rendition("devto")}).json()
    pub_id = job["result"]["publication_id"]
    r = client.post(f"/api/v1/publications/{pub_id}/metrics", headers={"Idempotency-Key": uuid.uuid4().hex}).json()
    assert r["metrics"]["views"] == 340 and "shares" in r["unavailable"]
    stored = client.get(f"/api/v1/publications/{pub_id}").json()
    assert stored["metrics"]["likes"] == 21 and stored["metrics_at"]
