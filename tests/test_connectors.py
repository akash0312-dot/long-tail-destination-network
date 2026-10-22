"""Ghost and Lemmy connectors over the HTTP contract, against the mock platforms (simulation mode).
Each test maps to a hard case from the brief."""
import time

import httpx
import pytest

TARGETS = {
    "ghost": {"destination": {"name": "Blog A", "params": {"base_url": "https://blog-a.example.com"}},
              "connection": {}, "instance": "sim/ghost/blog-a-example-com"},
    "lemmy": {"destination": {"name": "Lemmy A", "params": {"base_url": "https://lemmy-a.example",
                                                            "community": "longtail_test"}},
              "connection": {"credentials": {"username": "longtail_bot", "password": "pw-test-123"}},
              "instance": "sim/lemmy/lemmy-a-example"},
    "discourse": {"destination": {"name": "Forum A", "params": {"base_url": "https://forum-a.example",
                                                                "category_id": 5}},
                  "connection": {"credentials": {"api_key": "k-test-123456", "api_username": "longtail_bot"}},
                  "instance": "sim/discourse/forum-a-example"},
    "devto": {"destination": {"name": "Forem A", "params": {"base_url": "https://forem-a.example"}},
              "connection": {"credentials": {"api_key": "devto-test-key-1"}},
              "instance": "sim/devto/forem-a-example"},
}
RENDITION = {"title": "How small creator teams ship video weekly", "body": "A short **discussion** starter.",
             "content_type": "text", "tags": ["video"]}


def call(client, key, method, **body):
    r = client.post(f"/connector/v1/{key}/{method}", json=body)
    return r.status_code, r.json()


def publish(client, key, idem="key-1", rendition=None, target=None):
    t = target or TARGETS[key]
    return call(client, key, "publish", rendition=rendition or RENDITION, destination=t["destination"],
                connection=t["connection"], idempotency_key=idem)


def post_ref(key, pid):
    t = TARGETS[key]
    return {"platform_post_id": pid, "connection": t["connection"], "destination": t["destination"]}


@pytest.mark.parametrize("key", TARGETS)
def test_full_certification_cycle(client, mock, key):
    status, res = publish(client, key)
    assert status == 200, res
    assert res["status"] == "published" and res["platform_post_id"] and res["warnings"] == []
    assert httpx.get(res["live_url"]).status_code == 200  # live URL really serves the post

    status, st = call(client, key, "get_status", **post_ref(key, res["platform_post_id"]))
    assert status == 200 and st["status"] == "published"

    status, d = call(client, key, "delete", **post_ref(key, res["platform_post_id"]))
    assert status == 200 and d["deleted"] is True

    status, st = call(client, key, "get_status", **post_ref(key, res["platform_post_id"]))
    assert st["status"] == "deleted"
    assert httpx.get(res["live_url"]).status_code == 404


@pytest.mark.parametrize("key", TARGETS)
def test_timeout_then_retry_same_key_never_duplicates(client, mock, key):
    """Hard case 1: the platform creates the post but the answer arrives after our timeout."""
    mock.arm(platform=key, op="publish", kind="timeout", delay_s=2)
    status, err = publish(client, key, idem="retry-key")
    assert status == 503
    assert err["error"]["code"] == "PLATFORM_UNAVAILABLE" and err["error"]["retryable"] is True
    assert err["error"]["details"]["timeout"] is True
    time.sleep(1.2)  # let the slow mock response finish

    status, res = publish(client, key, idem="retry-key")
    assert status == 200, res
    assert any(w.startswith("IDEMPOTENT_REPLAY") for w in res["warnings"])
    posts = mock.posts(key, TARGETS[key]["instance"])
    assert len(posts) == 1, "retry must not create a second post"

    status, again = publish(client, key, idem="retry-key")
    assert again["platform_post_id"] == res["platform_post_id"]
    assert len(mock.posts(key, TARGETS[key]["instance"])) == 1


@pytest.mark.parametrize("key", TARGETS)
def test_429_short_reset_backs_off_then_succeeds(client, mock, key):
    mock.arm(platform=key, op="publish", kind="rate_limit", reset_s=1)
    started = time.monotonic()
    status, res = publish(client, key)
    assert status == 200, res
    assert time.monotonic() - started >= 1.0, "must wait for the platform's reset time"


@pytest.mark.parametrize("key", TARGETS)
def test_429_long_reset_returns_rate_limit_with_retry_time(client, mock, key):
    """Hard case 2a: back off using the reset time; long waits go back to the runtime to reschedule."""
    mock.arm(platform=key, op="publish", kind="rate_limit", reset_s=30)
    status, err = publish(client, key)
    assert status == 429
    e = err["error"]
    assert e["code"] == "PLATFORM_RATE_LIMIT" and e["retryable"] is True
    assert 25 <= e["details"]["retry_after_s"] <= 30
    assert e["details"]["reset_source"] == "Retry-After"


@pytest.mark.parametrize("key", TARGETS)
def test_expired_token_maps_to_connection_auth_expired(client, mock, key):
    """Hard case 2b. Lemmy re-logs in once first, so the fault is armed twice."""
    mock.arm(platform=key, op="publish", kind="auth_expired", times=2)
    status, err = publish(client, key)
    assert err["error"]["code"] == "CONNECTION_AUTH_EXPIRED", err
    assert status == 401 and err["error"]["retryable"] is False


def test_lemmy_wrong_password_is_auth_expired(client, mock):
    t = dict(TARGETS["lemmy"], connection={"credentials": {"username": "longtail_bot", "password": "wrong-password"}})
    status, err = publish(client, "lemmy", target=t)
    assert err["error"]["code"] == "CONNECTION_AUTH_EXPIRED"


def test_lemmy_session_expiry_relogs_in_transparently(client, mock):
    mock.arm(platform="lemmy", op="publish", kind="auth_expired", times=1)
    status, res = publish(client, "lemmy")
    assert status == 200 and res["status"] == "published"


@pytest.mark.parametrize("key", TARGETS)
def test_content_rejected_returns_platform_message(client, mock, key):
    """Hard case 2c."""
    mock.arm(platform=key, op="publish", kind="reject", message="spam_detected")
    status, err = publish(client, key)
    assert status == 422
    assert err["error"]["code"] == "PLATFORM_CONTENT_REJECTED" and err["error"]["retryable"] is False
    assert "spam_detected" in err["error"]["details"]["platform_message"]


@pytest.mark.parametrize("key", TARGETS)
def test_renamed_api_field_is_schema_changed(client, mock, key):
    """Hard case 5 (connector half): a renamed field is detected, not silently ignored."""
    mock.arm(platform=key, op="publish", kind="rename_field")
    status, err = publish(client, key)
    assert err["error"]["code"] == "PLATFORM_SCHEMA_CHANGED"
    assert err["error"]["details"]["missing_field"]


@pytest.mark.parametrize("key", TARGETS)
def test_title_too_long_blocked_before_any_platform_call(client, mock, key):
    """Hard case 3a: field-level reasons, nothing sent to the platform."""
    before = mock.calls()
    status, err = publish(client, key, rendition=dict(RENDITION, title="x" * 300))
    assert status == 422
    assert err["error"]["code"] == "DEST_SPEC_VIOLATION"
    v = err["error"]["details"]["violations"][0]
    assert v["field"] == "title" and v["code"] == "TITLE_TOO_LONG" and v["actual"] == 300
    assert mock.calls() == before


@pytest.mark.parametrize("key", TARGETS)
def test_unsupported_media_reported_by_validate(client, key):
    """Hard case 3b."""
    t = TARGETS[key]
    r = dict(RENDITION, media=[{"url": "https://cdn.example.com/photo.heic", "type": "image"}])
    status, res = call(client, key, "validate", rendition=r, destination=t["destination"], connection=t["connection"])
    assert status == 200 and res["ok"] is False
    assert res["violations"][0]["code"] == "MEDIA_TYPE_UNSUPPORTED"
    assert res["violations"][0]["actual"] == "image/heic"


@pytest.mark.parametrize("key", TARGETS)
def test_circuit_breaker_stops_after_five_failures(client, mock, key):
    mock.arm(platform=key, op="*", kind="server_error", times=50)
    for i in range(5):
        status, err = publish(client, key, idem=f"cb-{i}")
        assert err["error"]["code"] == "PLATFORM_UNAVAILABLE"
    calls = mock.calls()
    status, err = publish(client, key, idem="cb-final")
    assert err["error"]["code"] == "PLATFORM_UNAVAILABLE" and err["error"]["details"]["circuit_open"] is True
    assert mock.calls() == calls, "open circuit must not call the platform"


@pytest.mark.parametrize("key", TARGETS)
def test_one_connector_many_destinations(client, mock, key):
    """Must-have 3: a second instance of the same platform type needs only different destination params."""
    t = TARGETS[key]
    other = dict(t, destination={**t["destination"],
                                 "params": {**t["destination"]["params"], "base_url": "https://second.example.org"}})
    assert publish(client, key)[0] == 200
    assert publish(client, key, target=other)[0] == 200
    assert len(mock.posts(key, t["instance"])) == 1
    assert len(mock.posts(key, f"sim/{key}/second-example-org")) == 1
