"""Demo console: the page, uploaded videos served at a public link, and the formats the page picks per platform."""
import uuid

import pytest

from app.config import get_settings

VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"0" * 2048


def _key() -> dict:
    return {"Idempotency-Key": uuid.uuid4().hex}


def test_console_page_is_served_and_the_root_opens_it(client):
    page = client.get("/console")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "Demo console" in page.text and "/api/v1/uploads" in page.text
    root = client.get("/", follow_redirects=False)
    assert root.status_code in (302, 307) and root.headers["location"] == "/console"


def test_uploaded_video_gets_a_public_link_while_the_upload_needs_the_token(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_token", "demo-token-123456")
    upload = {"file": ("clip.mp4", VIDEO, "video/mp4")}
    assert client.post("/api/v1/uploads", files=upload, headers=_key()).status_code == 401
    r = client.post("/api/v1/uploads", files=upload,
                    headers={"Authorization": "Bearer demo-token-123456", **_key()})
    assert r.status_code == 201, r.json()
    body = r.json()
    assert (body["mime_type"], body["size_bytes"], body["filename"]) == ("video/mp4", len(VIDEO), "clip.mp4")
    got = client.get(body["url"].replace("http://testserver", ""))  # no token: platforms and feed readers fetch it
    assert got.status_code == 200 and got.content == VIDEO
    assert got.headers["content-type"] == "video/mp4"


def test_upload_refuses_other_file_types_and_files_over_the_limit(client, monkeypatch):
    r = client.post("/api/v1/uploads", files={"file": ("notes.txt", b"hello", "text/plain")}, headers=_key())
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    big = {"file": ("big.mp4", b"0" * (2 * 1_048_576), "video/mp4")}
    r = client.post("/api/v1/uploads", files=big, headers=_key())
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.parametrize("key", ["run.json", "a" * 32 + ".mp4", "A" * 32 + ".exe"])
def test_media_link_serves_only_uploaded_files(client, key):
    assert client.get(f"/api/v1/media/{key}").status_code == 404


def test_connectors_list_the_formats_the_console_picks_from(client):
    types = {c["key"]: c["content_types"] for c in client.get("/api/v1/connectors").json()["connectors"]}
    assert "video" in types["peertube"] and "video" in types["mrss"]
    assert "video" not in types["ghost"] and "video_link" in types["ghost"]
    assert types["devto"] == ["article", "text"]


def test_one_uploaded_video_reaches_every_platform_in_its_own_format(client, mock):
    """What the console's Publish button does: one post per destination, the format chosen from the connector."""
    from tests.test_certification import make_dest
    url = client.post("/api/v1/uploads", files={"file": ("clip.mp4", VIDEO, "video/mp4")}, headers=_key()).json()["url"]
    types = {c["key"]: c["content_types"] for c in client.get("/api/v1/connectors").json()["connectors"]}
    base = {"title": "How I edit a 60-second creator video", "language": "en", "tags": ["video", "editing"],
            "body": "A short walkthrough of my editing workflow: rough cut, captions, music and export."}
    for key in ("ghost", "lemmy", "discourse", "devto", "hashnode", "peertube", "webhook", "assisted"):
        d, _ = make_dest(client, key, rules={"link_policy": {"allowed": True, "max_links": 1},
                                             "rules_url": "https://example.org/rules"})
        if "video" in types[key]:
            rendition = {**base, "content_type": "video", "link_url": url,
                         "media": [{"type": "video", "url": url, "mime_type": "video/mp4"}]}
        elif "video_link" in types[key]:
            rendition = {**base, "content_type": "video_link", "link_url": url}
        else:
            rendition = {**base, "content_type": "article", "body": f"{base['body']}\n\nWatch the video: {url}"}
        job = client.post("/api/v1/publications", headers=_key(),
                          json={"destination_id": d["id"], "rendition": rendition, "tailor": True}).json()
        job = client.get(f"/api/v1/jobs/{job['job_id']}").json()
        assert job["status"] == "succeeded", (key, job["error"])
        assert job["result"]["status"] == ("awaiting_human" if key == "assisted" else "published"), key
