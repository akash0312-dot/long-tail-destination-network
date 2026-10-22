"""Tailoring: one content item -> a rendition that fits each destination, with every change listed."""
import uuid
from types import SimpleNamespace

from app.connectors.registry import get_connector
from app.tailoring import discussion_title, truncate_words, tailor

CONTENT = {"title": "We cut our video editing time by 40%", "content_type": "article",
           "body": "Here is our batching workflow. #VideoEditing #creators\nSign up for our free trial today!",
           "link_url": "https://creator.example/blog/editing-workflow", "tags": ["Video Editing", "AI Tools", "SaaS",
                                                                                 "Creators", "Workflow", "Extra"],
           "mentions": ["@alice", "bob@lemmy.example"], "language": "en"}


def run(connector_key, **dest):
    surface = get_connector(connector_key).manifest.surfaces[0]
    return tailor(dict(CONTENT), SimpleNamespace(language="en", category=None, **dest), connector_key, surface)


def test_truncation_never_cuts_inside_a_word():
    assert truncate_words("one two three four", 13) == "one two…"
    assert truncate_words("short", 10) == "short"
    assert len(truncate_words("word " * 100, 50)) <= 50


def test_discussion_titles():
    assert discussion_title("How do you batch-record?") == "How do you batch-record?"
    assert discussion_title("How we batch-record") == "How we batch-record?"
    assert discussion_title("We cut editing time by 40%") == "We cut editing time by 40%: what has worked for you?"


def test_forum_gets_discussion_first_framing():
    r, changes = run("discourse")
    assert r["title"].endswith("?")
    assert "free trial" not in r["body"], "call-to-action lines are removed for forums"
    assert "#VideoEditing" not in r["body"] and "inline hashtags moved to tags" in changes
    assert r["tags"][:2] == ["video-editing", "ai-tools"], "the creator's explicit tags come first within the cap"
    assert r["body"].rstrip().endswith("What has worked for you?") or "?" in r["body"]
    assert "Source: https://creator.example/blog/editing-workflow" in r["body"]
    assert len(r["tags"]) <= 5 and any("discussion" in c for c in changes)


def test_article_keeps_canonical_url_and_platform_tags():
    r, changes = run("devto")
    assert r["canonical_url"] == CONTENT["link_url"] and "canonical URL set to the original article" in changes
    assert r["tags"] == ["videoediting", "aitools", "saas", "creators"], "Forem: 4 tags, lowercase letters and digits"
    assert r["title"] == CONTENT["title"], "articles keep their own title"


def test_mentions_follow_platform_style():
    r, changes = run("lemmy")
    assert r["mentions"] == ["@bob@lemmy.example"] and any("dropped mentions" in c for c in changes)
    r, _ = run("devto")
    assert r["mentions"] == ["@alice", "@bob"]


def test_long_title_truncated_to_platform_limit():
    surface = get_connector("devto").manifest.surfaces[0]
    r, changes = tailor({**CONTENT, "title": "word " * 60}, SimpleNamespace(language="en", category=None), "devto",
                        surface)
    assert len(r["title"]) <= 128 and r["title"].endswith("…")
    assert any("truncated at a word boundary" in c for c in changes)


def test_language_mismatch_is_flagged_not_translated():
    _, changes = tailor(dict(CONTENT), SimpleNamespace(language="de", category=None), "discourse",
                        get_connector("discourse").manifest.surfaces[0])
    assert any(c.startswith("NEEDS_TRANSLATION") for c in changes)


def _dest(client, key, url, **extra):
    return client.post("/api/v1/destinations", headers={"Idempotency-Key": uuid.uuid4().hex},
                       json={"name": f"{key} {uuid.uuid4().hex[:6]}", "url": url, "connector_key": key, **extra}).json()


def test_tailor_preview_for_several_destinations(client):
    forum = _dest(client, "discourse", f"https://forum-{uuid.uuid4().hex[:6]}.example", params={"category_id": 1})
    blog = _dest(client, "devto", f"https://forem-{uuid.uuid4().hex[:6]}.example")
    r = client.post("/api/v1/tailor", headers={"Idempotency-Key": uuid.uuid4().hex},
                    json={"content": CONTENT, "destination_ids": [forum["id"], blog["id"]]})
    assert r.status_code == 200, r.json()
    items = {i["connector_key"]: i for i in r.json()["items"]}
    assert items["discourse"]["rendition"]["title"].endswith("?")
    assert items["devto"]["rendition"]["canonical_url"] == CONTENT["link_url"]
    # the preview shows problems before anything is posted: the article keeps its sign-up line, and destinations
    # ban self-promotion unless told otherwise
    v = items["devto"]["validation"]
    assert v["ok"] is False and v["violations"][0]["code"] == "DEST_RULE_SELF_PROMO"
    assert items["discourse"]["validation"]["ok"] is True, "the forum version dropped the call-to-action line"


def test_publish_with_tailor_reports_changes(client, mock):
    url = f"https://forum-{uuid.uuid4().hex[:6]}.example"
    conn = client.post("/api/v1/connections", headers={"Idempotency-Key": uuid.uuid4().hex},
                       json={"connector_key": "discourse", "name": "bot", "secret_refs": {"api_key": "env:NOPE_K"},
                             "params": {"api_username": "bot"}}).json()
    d = _dest(client, "discourse", url, params={"category_id": 1}, connection_id=conn["id"])
    job = client.post("/api/v1/publications", headers={"Idempotency-Key": uuid.uuid4().hex},
                      json={"destination_id": d["id"], "rendition": CONTENT, "tailor": True}).json()
    assert job["status"] == "succeeded", job["error"]
    assert any(w.startswith("TAILORED: title reframed") for w in job["result"]["warnings"])
