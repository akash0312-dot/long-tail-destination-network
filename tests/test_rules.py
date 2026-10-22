"""Posting rules. RULE_CASES is the rule test set behind the 'Rule safety' metric (caught vs missed)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.rules import evaluate

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
OWN = ["creatorstudio.example"]
NEUTRAL = {"title": "How do small teams plan a weekly video schedule?",
           "body": "We batch-record on Mondays and edit midweek. What works for you?", "language": "en"}
NO_PROMO = {"self_promo_allowed": False}
WEEKLY_1 = {"frequency_cap": {"posts": 1, "per_days": 7}}

# (case id, rules, rendition overrides, recent post ages in hours, expected action, expected code)
RULE_CASES = [
    ("neutral-allowed", NO_PROMO, {}, [], "allow", None),
    ("promo-flag", NO_PROMO, {"is_promotional": True}, [], "block", "DEST_RULE_SELF_PROMO"),
    ("promo-cta", NO_PROMO, {"body": "Sign up for our free trial today!"}, [], "block", "DEST_RULE_SELF_PROMO"),
    ("promo-discount", NO_PROMO, {"body": "Get 30% off this week only."}, [], "block", "DEST_RULE_SELF_PROMO"),
    ("promo-own-link", NO_PROMO, {"body": "Details: https://creatorstudio.example/launch"}, [], "block",
     "DEST_RULE_SELF_PROMO"),
    ("promo-allowed-here", {"self_promo_allowed": True}, {"is_promotional": True}, [], "allow", None),
    ("cap-second-post", WEEKLY_1, {}, [24], "reschedule", "DEST_RULE_FREQUENCY_CAP"),
    ("cap-old-post-ok", WEEKLY_1, {}, [24 * 8], "allow", None),
    ("cap-3-per-day", {"frequency_cap": {"posts": 3, "per_days": 1}}, {}, [1, 2], "allow", None),
    ("cap-3-per-day-full", {"frequency_cap": {"posts": 3, "per_days": 1}}, {}, [1, 2, 3], "reschedule",
     "DEST_RULE_FREQUENCY_CAP"),
    ("links-banned", {"link_policy": {"allowed": False}}, {"body": "See https://example.org/guide"}, [], "block",
     "DEST_RULE_LINK_POLICY"),
    ("links-too-many", {"link_policy": {"max_links": 1}},
     {"body": "https://a.example/1 and https://b.example/2"}, [], "block", "DEST_RULE_LINK_POLICY"),
    ("links-domain", {"link_policy": {"allowed_domains": ["github.com"]}}, {"link_url": "https://evil.example/x"},
     [], "block", "DEST_RULE_LINK_POLICY"),
    ("links-subdomain-ok", {"link_policy": {"allowed_domains": ["github.com"]}},
     {"link_url": "https://gist.github.com/x"}, [], "allow", None),
    ("links-shortener", {"link_policy": {}}, {"body": "watch https://bit.ly/abc"}, [], "block",
     "DEST_RULE_LINK_POLICY"),
    ("lang-mismatch", {"language": "de"}, {"language": "en"}, [], "block", "DEST_RULE_LANGUAGE_MISMATCH"),
    ("lang-match-region", {"language": "pt-BR"}, {"language": "pt"}, [], "allow", None),
    ("automation-banned", {"automation_allowed": False}, {}, [], "block", "DEST_RULE_AUTOMATION_NOT_ALLOWED"),
    ("disclosure-added", {"disclosure_required": True}, {"ai_generated": True}, [], "allow", None),
    ("disclosure-present", {"disclosure_required": True},
     {"ai_generated": True, "body": "Made with AI assistance. #AIgenerated"}, [], "allow", None),
    ("promo-and-cap", {**NO_PROMO, **WEEKLY_1}, {"is_promotional": True}, [24], "block", "DEST_RULE_SELF_PROMO"),
]


@pytest.mark.parametrize("case,rules,overrides,ages_h,action,code", RULE_CASES, ids=[c[0] for c in RULE_CASES])
def test_rule_case(case, rules, overrides, ages_h, action, code):
    d = evaluate({**NEUTRAL, **overrides}, rules, [NOW - timedelta(hours=h) for h in ages_h], NOW, OWN)
    assert d.action == action, d.reason
    assert d.code == code
    if action != "allow":
        assert d.reason, "a blocked or rescheduled post must say why"


def test_reschedule_points_to_next_free_slot():
    d = evaluate(NEUTRAL, WEEKLY_1, [NOW - timedelta(days=2)], NOW, OWN)
    assert d.not_before == NOW + timedelta(days=5)


def test_test_posts_are_exempt_from_cap_only():
    assert evaluate(NEUTRAL, WEEKLY_1, [NOW], NOW, OWN, is_test=True).action == "allow"
    assert evaluate({**NEUTRAL, "is_promotional": True}, NO_PROMO, [], NOW, OWN, is_test=True).action == "block"


def test_disclosure_uses_destination_language():
    d = evaluate({**NEUTRAL, "language": "de", "ai_generated": True},
                 {"disclosure_required": True, "language": "de"}, [], NOW, OWN)
    assert d.action == "allow"
    assert "KI" in d.rendition["body"] and any(w.startswith("AI_DISCLOSURE_ADDED") for w in d.warnings)


# ------------------------------------------------------------------ through the API (hard cases 4 and 7)
def _dest(client, **rules):
    url = f"https://lemmy-{uuid.uuid4().hex[:6]}.example"
    body = {"name": "rules test", "url": f"{url}/c/test", "connector_key": "lemmy",
            "params": {"base_url": url, "community": "test"}, **rules}
    conn = client.post("/api/v1/connections", headers={"Idempotency-Key": uuid.uuid4().hex},
                       json={"connector_key": "lemmy", "name": "bot", "secret_refs": {"username": "env:U",
                                                                                      "password": "env:P"}}).json()
    body["connection_id"] = conn["id"]
    return client.post("/api/v1/destinations", json=body, headers={"Idempotency-Key": uuid.uuid4().hex}).json()


def _publish(client, dest_id, rendition, **extra):
    return client.post("/api/v1/publications", headers={"Idempotency-Key": uuid.uuid4().hex},
                       json={"destination_id": dest_id, "rendition": rendition, **extra}).json()


def test_second_post_inside_weekly_cap_is_rescheduled(client, mock):
    d = _dest(client, frequency_cap={"posts": 1, "per_days": 7})
    first = _publish(client, d["id"], NEUTRAL)
    assert first["status"] == "succeeded"
    second = _publish(client, d["id"], {**NEUTRAL, "title": "A second, different question for this week"})
    assert second["status"] == "failed"
    err = second["error"]
    assert err["code"] == "DEST_RULE_FREQUENCY_CAP" and err["retryable"] is True
    assert "rescheduled" in err["message"] and err["details"]["retry_at"]
    follow = client.get(f"/api/v1/jobs/{err['details']['rescheduled_job_id']}").json()
    assert follow["status"] == "queued" and follow["run_after"]


def test_cap_reject_mode_does_not_reschedule(client, mock):
    d = _dest(client, frequency_cap={"posts": 1, "per_days": 7})
    _publish(client, d["id"], NEUTRAL)
    second = _publish(client, d["id"], {**NEUTRAL, "title": "Another one"}, on_cap="reject")
    assert second["error"]["code"] == "DEST_RULE_FREQUENCY_CAP"
    assert "rescheduled_job_id" not in second["error"]["details"]


def test_promo_post_to_no_promo_community_is_blocked_with_reason(client, mock):
    d = _dest(client, self_promo_allowed=False)
    job = _publish(client, d["id"], {**NEUTRAL, "body": "Try our app — use code LAUNCH for 20% off!"})
    assert job["status"] == "failed" and job["error"]["code"] == "DEST_RULE_SELF_PROMO"
    assert "promotional" in job["error"]["message"]
    inst = "sim/lemmy/" + d["params"]["base_url"].split("//")[1].replace(".", "-")
    assert mock.posts("lemmy", inst) == [], "nothing may reach the platform"


def test_ai_disclosure_appended_for_destination_that_requires_it(client, mock):
    d = _dest(client, disclosure_required=True)
    job = _publish(client, d["id"], {**NEUTRAL, "ai_generated": True})
    assert job["status"] == "succeeded"
    assert any(w.startswith("AI_DISCLOSURE_ADDED") for w in job["result"]["warnings"])
    inst = "sim/lemmy/" + d["params"]["base_url"].split("//")[1].replace(".", "-")
    assert "created with the help of AI" in mock.posts("lemmy", inst)[0]["body"]


def test_non_english_community_rejects_english_content(client, mock):
    d = _dest(client, language="ja")
    job = _publish(client, d["id"], NEUTRAL)
    assert job["error"]["code"] == "DEST_RULE_LANGUAGE_MISMATCH" and "'ja'" in job["error"]["message"]
    ok = _publish(client, d["id"], {**NEUTRAL, "title": "小規模チームの動画制作スケジュール", "language": "ja"})
    assert ok["status"] == "succeeded"


def test_contract_publish_also_enforces_rules(client, mock):
    """Rules carried on the destination are checked even when the runtime calls the connector directly."""
    r = client.post("/connector/v1/lemmy/publish", json={
        "rendition": {**NEUTRAL, "is_promotional": True},
        "destination": {"params": {"base_url": "https://direct.example", "community": "c"},
                        "self_promo_allowed": False},
        "connection": {"credentials": {"username": "u", "password": "p-123456"}}, "idempotency_key": "direct-1"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "DEST_RULE_SELF_PROMO"


def test_uncertified_destination_refused_when_certification_required(client, mock, monkeypatch):
    d = _dest(client)
    monkeypatch.setattr(get_settings(), "require_certification", True)
    job = _publish(client, d["id"], NEUTRAL)
    assert job["error"]["code"] == "DEST_NOT_CERTIFIED"
