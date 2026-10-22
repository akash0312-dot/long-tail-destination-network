"""MRSS feed connector: items in a hosted Media RSS feed that CTV apps and aggregators pull."""
import uuid
from xml.etree import ElementTree as ET

from app.config import get_settings

MEDIA = "{http://search.yahoo.com/mrss/}"
VIDEO = {"title": "Five-minute lighting setup for small studios", "body": "A quick walkthrough.",
         "content_type": "video", "canonical_url": "https://creator.example/lighting",
         "media": [{"url": "https://cdn.example/lighting.mp4", "type": "video", "mime_type": "video/mp4"},
                   {"url": "https://cdn.example/lighting.jpg", "type": "image", "mime_type": "image/jpeg"}]}


def _feed_key() -> str:
    return f"ctv-{uuid.uuid4().hex[:8]}"


def _call(client, method, **body):
    return client.post(f"/connector/v1/mrss/{method}", json=body).json()


def _items(client, key):
    r = client.get(f"/api/v1/feeds/{key}.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/rss+xml")
    return ET.fromstring(r.content).find("channel").findall("item")


def test_publish_adds_valid_media_rss_item_and_delete_removes_it(client):
    key = _feed_key()
    dest = {"name": "CTV main feed", "params": {"feed_key": key}}
    res = _call(client, "publish", rendition=VIDEO, destination=dest, idempotency_key="feed-1")
    assert res["status"] == "published" and res["live_url"].endswith(f"/api/v1/feeds/{key}.xml#{res['platform_post_id']}")
    (item,) = _items(client, key)
    assert item.findtext("guid") == res["platform_post_id"] and item.findtext("link") == VIDEO["canonical_url"]
    content = item.find(f"{MEDIA}content")
    assert content.get("url") == "https://cdn.example/lighting.mp4" and content.get("medium") == "video"
    assert item.find(f"{MEDIA}thumbnail").get("url") == "https://cdn.example/lighting.jpg"

    again = _call(client, "publish", rendition=VIDEO, destination=dest, idempotency_key="feed-1")
    assert again["platform_post_id"] == res["platform_post_id"] and again["warnings"][0].startswith("IDEMPOTENT")
    assert len(_items(client, key)) == 1, "retry must not add a second item"

    ref = {"platform_post_id": res["platform_post_id"], "destination": dest}
    assert _call(client, "get_status", **ref)["status"] == "published"
    assert _call(client, "delete", **ref) == {"deleted": True}
    assert _call(client, "get_status", **ref)["status"] == "deleted"
    assert _items(client, key) == []


def test_feed_needs_a_video(client):
    res = _call(client, "validate", rendition={"title": "Text only post", "body": "x"},
                destination={"params": {"feed_key": _feed_key()}})
    codes = {v["code"] for v in res["violations"]}
    assert res["ok"] is False and {"VIDEO_REQUIRED", "CONTENT_TYPE_UNSUPPORTED"} <= codes


def test_feed_is_public_even_when_the_api_needs_a_token(client, monkeypatch):
    key = _feed_key()
    _call(client, "publish", rendition=VIDEO, destination={"params": {"feed_key": key}}, idempotency_key="pub-1")
    monkeypatch.setattr(get_settings(), "api_token", "demo-token-123456")
    assert client.get(f"/api/v1/feeds/{key}.xml").status_code == 200
    assert client.get("/api/v1/destinations").status_code == 401


def test_feed_destination_certifies_at_level_l2(client):
    key = _feed_key()
    dest = client.post("/api/v1/destinations", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "name": f"Video aggregator feed {key}", "url": f"https://aggregator.example/{key}", "connector_key": "mrss",
        "params": {"feed_key": key}, "frequency_cap": {"posts": 20, "per_days": 1},
        "link_policy": {"allowed": True}}).json()
    job = client.post("/api/v1/certifications", headers={"Idempotency-Key": uuid.uuid4().hex},
                      json={"destination_id": dest["id"]}).json()
    job = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert job["status"] == "succeeded", job["error"]
    assert job["result"]["level"] == "L2"
    assert _items(client, key) == [], "the certification item is removed again"
