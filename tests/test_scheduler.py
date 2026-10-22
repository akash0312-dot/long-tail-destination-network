"""Outstanding: recertification before 30 days; failures demote, exclude and open a ticket."""
import uuid
from datetime import timedelta

from app.connectors.http import limiter
from app.db import session_scope
from app.models import Destination, utcnow
from app.services.scheduler import recertify_due
from tests.test_certification import certify, make_dest


def _age(dest_id: str, days: int) -> None:
    with session_scope() as db:
        db.get(Destination, dest_id).last_certified_at = utcnow() - timedelta(days=days)


def _dest_row(client, dest_id):
    return client.get(f"/api/v1/destinations/{dest_id}").json()


def test_due_destination_is_recertified_and_recent_one_is_left_alone(client, mock):
    old, _ = make_dest(client, "discourse")
    fresh, _ = make_dest(client, "discourse")
    assert certify(client, old["id"])["status"] == "succeeded"
    assert certify(client, fresh["id"])["status"] == "succeeded"
    _age(old["id"], 26)
    before = _dest_row(client, old["id"])["last_certified_at"]
    summary = recertify_due()
    queued = {q["destination_id"] for q in summary["queued"]}
    assert old["id"] in queued and fresh["id"] not in queued
    after = _dest_row(client, old["id"])
    assert after["certification_status"] == "certified" and after["last_certified_at"] > before


def test_failing_recertification_demotes_excludes_and_opens_one_ticket(client, mock):
    d, _ = make_dest(client, "devto")
    assert certify(client, d["id"])["status"] == "succeeded"
    _age(d["id"], 29)
    inst = "sim/devto/" + d["url"].split("//")[1].replace(".", "-")
    mock.arm(platform="devto", instance=inst, op="get_status", kind="rename_field")
    recertify_due()
    row = _dest_row(client, d["id"])
    assert row["certification_status"] == "failed" and row["level"] == "L0"
    tickets = client.get("/api/v1/tickets", params={"destination_id": d["id"], "status": "open"}).json()["items"]
    assert len(tickets) == 1 and "status_published" in tickets[0]["details"]["reason"]
    rec = client.post("/api/v1/recommendations", headers={"Idempotency-Key": uuid.uuid4().hex},
                      json={"content": {"title": "anything", "body": "x"}, "include_excluded": True}).json()
    assert any(e["destination_id"] == d["id"] and "demoted" in e["excluded"] for e in rec["excluded"])

    limiter.reset()  # the client-side rate limit (10 per 30 s for Forem) would otherwise refuse back-to-back runs
    mock.arm(platform="devto", instance=inst, op="get_status", kind="rename_field")
    certify(client, d["id"])  # fails again: same ticket, one more occurrence
    limiter.reset()
    tickets = client.get("/api/v1/tickets", params={"destination_id": d["id"]}).json()["items"]
    assert len(tickets) == 1 and tickets[0]["occurrences"] == 2

    fixed = certify(client, d["id"])  # fault gone: passes, ticket resolved
    assert fixed["status"] == "succeeded", fixed["error"]["message"]
    (ticket,) = client.get("/api/v1/tickets", params={"destination_id": d["id"]}).json()["items"]
    assert ticket["status"] == "resolved" and ticket["resolved_at"]
    assert _dest_row(client, d["id"])["certification_status"] == "certified"


def test_temporary_failure_is_inconclusive_not_a_demotion(client, mock):
    """A 429 or timeout during recertification says nothing about the destination: keep it, retry later."""
    d, _ = make_dest(client, "discourse")
    assert certify(client, d["id"])["status"] == "succeeded"
    level = _dest_row(client, d["id"])["level"]
    inst = "sim/discourse/" + d["url"].split("//")[1].replace(".", "-")
    mock.arm(platform="discourse", instance=inst, op="publish", kind="rate_limit", reset_s=600)
    job = certify(client, d["id"])
    err = job["error"]
    assert err["details"]["inconclusive"] is True and err["retryable"] is True and "keeps level" in err["message"]
    retry = client.get(f"/api/v1/jobs/{err['details']['rescheduled_job_id']}").json()
    assert retry["status"] == "queued" and retry["run_after"]
    row = _dest_row(client, d["id"])
    assert row["certification_status"] == "certified" and row["level"] == level
    assert client.get("/api/v1/tickets", params={"destination_id": d["id"]}).json()["items"] == []


def test_run_endpoint_reports_due_destinations(client, mock):
    r = client.post("/api/v1/recertifications/run", headers={"Idempotency-Key": uuid.uuid4().hex}).json()
    assert {"due", "queued", "skipped", "cutoff"} <= set(r)
