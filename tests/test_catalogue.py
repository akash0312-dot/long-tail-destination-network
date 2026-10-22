"""Advanced level: catalogue import (+1,000-row benchmark), relevance, AI rules capture with human confirm."""
import csv
import io
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.ai import AnthropicProvider
from app.errors import AppError


def _key():
    return {"Idempotency-Key": uuid.uuid4().hex}


def import_csv(client, text: str, headers=None, **form):
    files = {"file": ("destinations.csv", text.encode("utf-8"), "text/csv")}
    r = client.post("/api/v1/catalogue/imports", files=files, data={k: str(v).lower() for k, v in form.items()},
                    headers=headers or _key())
    assert r.status_code == 202, r.json()
    return client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()


def report_rows(client, job) -> list[dict]:
    data = client.get(job["result"]["links"]["report_csv"].replace("http://testserver", "")).content.decode()
    return list(csv.DictReader(io.StringIO(data)))


def hard_case_csv(mock_url: str, tag: str) -> str:
    rows = [
        ["name", "url", "connector_key", "language", "category", "audience_tags", "description", "frequency_cap",
         "self_promo_allowed", "link_policy"],
        [f"AI video creators {tag}", f"{mock_url}/pages/{tag}/lem/c/aivideo", "lemmy", "en", "community",
         "ai;video", "Creators using AI for video", "1/7d", "false", "max:1"],
        ["Duplicate with tracking", f"{mock_url.replace('127.0.0.1', '127.0.0.1')}/pages/{tag}/lem/c/AIVideo/"
                                    f"?utm_source=x", "lemmy", "en", "", "", "", "", "", ""],
        [f"SaaS founders forum {tag}", f"{mock_url}/pages/{tag}/forum", "discourse", "", "forum", "saas;founders",
         "A forum for SaaS founders and startups", "2/1d", "", ""],
        ["Bad URL", "not a url", "", "", "", "", "", "", "", ""],
        ["FTP URL", "ftp://files.example/x", "", "", "", "", "", "", "", ""],
        ["Big platform", "https://www.youtube.com/@somechannel", "", "", "", "", "", "", "", ""],
        [f"Comunidad de creadores {tag}", f"{mock_url}/pages/{tag}/es", "", "es", "", "", "Comunidad de creadores "
         "de video para la gente que hace contenido", "", "", ""],
        [f"Gründer Treff {tag}", f"{mock_url}/pages/{tag}/de", "webhook", "", "", "", "Die Community für Gründer und "
         "Startups mit einer Plattform", "", "maybe", ""],
        [f"動画クリエイター {tag}", f"{mock_url}/pages/{tag}/ja", "assisted", "en", "", "", "動画クリエイターのためのコミュニティ",
         "", "", ""],
        [f"Gone forum {tag}", f"{mock_url}/pages/dead/{tag}", "discourse", "en", "", "", "", "", "", ""],
        [f"Knitting circle {tag}", f"{mock_url}/pages/{tag}/knit", "assisted", "en", "", "", "yarn and patterns",
         "", "", ""],
    ]
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


def test_hard_case_csv_duplicates_bad_urls_mixed_languages(client, mock):
    """Hard case 6a."""
    tag = uuid.uuid4().hex[:6]
    job = import_csv(client, hard_case_csv(mock.url, tag))
    assert job["status"] == "succeeded", job["error"]
    s = job["result"]["summary"]
    assert s["rows"] == 11 and s["duplicates_in_file"] == 1 and s["unreachable"] == 1
    rows = {r["row"]: r for r in report_rows(client, job)}
    assert rows["3"]["status"] == "duplicate" and "duplicate of row 2" in rows["3"]["errors"]
    assert rows["5"]["status"] == "invalid" and "not a valid http(s) URL" in rows["5"]["errors"]
    assert rows["6"]["status"] == "invalid"
    assert "major platforms" in rows["7"]["errors"]
    assert rows["8"]["connector_key"] == "assisted" and "connector unknown" in rows["8"]["warnings"]
    assert rows["9"]["status"] == "invalid" and "self_promo_allowed" in rows["9"]["errors"]
    assert "language mismatch: declared 'en', text looks 'ja'" in rows["10"]["warnings"]
    assert rows["11"]["reachable"] == "False" and "unreachable" in rows["11"]["warnings"]
    assert rows["2"]["est_audience"] == "4321" and "enriched from lemmy" in rows["2"]["warnings"]
    assert rows["4"]["est_audience"] == "2500"
    assert rows["4"]["language"] == "en" and "detected 'en'" in rows["4"]["warnings"]
    lemmy = client.get(f"/api/v1/destinations/{rows['2']['destination_id']}").json()
    assert lemmy["params"] == {"base_url": f"{mock.url}/pages/{tag}/lem", "community": "aivideo"}
    assert lemmy["frequency_cap"] == {"posts": 1, "per_days": 7} and lemmy["link_policy"]["max_links"] == 1
    assert lemmy["level"] == "L0" and lemmy["relevance_score"] >= 4 and "video" in lemmy["relevance_reason"]
    knit = client.get(f"/api/v1/destinations/{rows['12']['destination_id']}").json()
    assert knit["relevance_score"] == 1
    assert job["result"]["manifest"]["providers"][0]["provider"] == "mock"


def test_reimport_reports_existing_and_can_update(client, mock):
    tag = uuid.uuid4().hex[:6]
    text = hard_case_csv(mock.url, tag)
    import_csv(client, text, check_reachability=False)
    again = import_csv(client, text, check_reachability=False)
    assert again["result"]["summary"]["imported"] == 0 and again["result"]["summary"]["existing"] == 6
    updated = import_csv(client, text, check_reachability=False, update_existing=True)
    assert updated["result"]["summary"]["updated"] == 6


def test_same_upload_with_same_key_is_one_job(client, mock):
    text = hard_case_csv(mock.url, uuid.uuid4().hex[:6])
    headers = _key()
    first = client.post("/api/v1/catalogue/imports", headers=headers,
                        files={"file": ("a.csv", text.encode(), "text/csv")}, data={"check_reachability": "false"})
    second = client.post("/api/v1/catalogue/imports", headers=headers,
                         files={"file": ("a.csv", text.encode(), "text/csv")}, data={"check_reachability": "false"})
    assert first.json()["job_id"] == second.json()["job_id"] and second.headers.get("Idempotent-Replayed") == "true"


def test_import_from_csv_url(client, mock):
    tag = uuid.uuid4().hex[:6]
    httpx.post(f"{mock.url}/__admin/pages", json={"path": f"pages/{tag}.csv", "body": hard_case_csv(mock.url, tag)})
    r = client.post("/api/v1/catalogue/imports", headers=_key(),
                    data={"csv_url": f"{mock.url}/pages/{tag}.csv", "check_reachability": "false"})
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded" and job["result"]["summary"]["rows"] == 11


def test_one_thousand_rows_under_five_minutes(client, mock):
    """Advanced: 1,000 rows < 5 minutes with a row-by-row report (reachability on, against the mock)."""
    tag = uuid.uuid4().hex[:6]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "url", "connector_key", "category", "audience_tags", "description"])
    kind, expected = {}, {"duplicate": 0, "invalid": 0, "dead": 0}
    for i in range(1000):
        if i % 20 == 7:  # same site as the previous row, written differently (trailing slash)
            w.writerow([f"dup {i}", f"{mock.url}/pages/{tag}/site-{i - 1}/", "assisted", "", "", ""])
            kind[i] = "duplicate" if kind.get(i - 1) == "site" else "site"
        elif i % 33 == 5:
            w.writerow([f"bad {i}", f"bad url {i}", "", "", "", ""])
            kind[i] = "invalid"
        elif i % 50 == 11:
            w.writerow([f"dead {i}", f"{mock.url}/pages/dead/{tag}-{i}", "assisted", "", "", ""])
            kind[i] = "dead"
        else:
            w.writerow([f"Destination {i} for video creators", f"{mock.url}/pages/{tag}/site-{i}", "assisted",
                        "community", "video;creators", "AI video tools discussion"])
            kind[i] = "site"
        if kind[i] in expected:
            expected[kind[i]] += 1
    started = time.monotonic()
    job = import_csv(client, buf.getvalue())
    elapsed = time.monotonic() - started
    assert job["status"] == "succeeded", job["error"]
    s = job["result"]["summary"]
    print(f"\n1,000-row import: {elapsed:.1f}s  summary={s}")
    assert s["rows"] == 1000 and elapsed < 300
    assert s["duplicates_in_file"] == expected["duplicate"] and s["invalid"] == expected["invalid"]
    assert s["unreachable"] == expected["dead"]
    assert len(report_rows(client, job)) == 1000, "one report line per input row"


RULES_HTML = """<html><head><title>Rules</title><script>var x = 'no bots';</script></head><body>
<h1>Community rules</h1><ol><li>No self-promotion. Do not advertise your products.</li>
<li>Members may post at most once per week.</li><li>No more than 2 links per post.</li>
<li>AI-generated content must be labelled with the AI flair.</li><li>Be kind.</li></ol></body></html>"""


def _dest_with_rules_page(client, mock, body=RULES_HTML):
    tag = uuid.uuid4().hex[:6]
    httpx.post(f"{mock.url}/__admin/pages", json={"path": f"pages/{tag}/rules", "body": body})
    return client.post("/api/v1/destinations", headers=_key(), json={
        "name": f"rules {tag}", "url": f"{mock.url}/pages/{tag}/c/x", "connector_key": "lemmy",
        "rules_url": f"{mock.url}/pages/{tag}/rules"}).json()


def test_rules_capture_proposes_and_a_person_confirms(client, mock):
    d = _dest_with_rules_page(client, mock)
    job = client.post(f"/api/v1/destinations/{d['id']}/rules/capture", headers=_key(), json={}).json()
    job = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert job["status"] == "succeeded", job["error"]
    res = job["result"]
    assert res["proposed"] == {"frequency_cap": {"posts": 1, "per_days": 7}, "self_promo_allowed": False,
                               "link_policy": {"allowed": True, "max_links": 2}, "disclosure_required": True,
                               "automation_allowed": None, "language": None}
    assert {e["field"] for e in res["evidence"]} >= {"frequency_cap", "self_promo_allowed", "link_policy"}
    before = client.get(f"/api/v1/destinations/{d['id']}").json()
    assert before["frequency_cap"] is None, "nothing is applied before a person confirms"
    confirmed = client.post(f"/api/v1/rule-proposals/{res['proposal_id']}/confirm", headers=_key(),
                            json={"reviewed_by": "akash", "overrides": {"automation_allowed": True}}).json()
    assert confirmed["status"] == "confirmed" and confirmed["applied"]["automation_allowed"] is True
    after = client.get(f"/api/v1/destinations/{d['id']}").json()
    assert after["frequency_cap"] == {"posts": 1, "per_days": 7} and after["disclosure_required"] is True
    again = client.post(f"/api/v1/rule-proposals/{res['proposal_id']}/confirm", headers=_key(),
                        json={"reviewed_by": "akash"})
    assert again.json()["error"]["message"] == "Proposal is already confirmed"


def test_rejected_proposal_changes_nothing(client, mock):
    d = _dest_with_rules_page(client, mock)
    job = client.post(f"/api/v1/destinations/{d['id']}/rules/capture", headers=_key(), json={}).json()
    pid = client.get(f"/api/v1/jobs/{job['job_id']}").json()["result"]["proposal_id"]
    r = client.post(f"/api/v1/rule-proposals/{pid}/reject", headers=_key(),
                    json={"reviewed_by": "akash", "note": "page is outdated"}).json()
    assert r["status"] == "rejected"
    assert client.get(f"/api/v1/destinations/{d['id']}").json()["frequency_cap"] is None


def test_rules_capture_respects_robots_txt(client, mock):
    d = _dest_with_rules_page(client, mock)
    httpx.post(f"{mock.url}/__admin/pages", json={"path": "robots.txt", "body": "User-agent: *\nDisallow: /pages/"})
    job = client.post(f"/api/v1/destinations/{d['id']}/rules/capture", headers=_key(), json={}).json()
    job = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert job["error"]["code"] == "RULES_PAGE_UNAVAILABLE" and "robots.txt" in job["error"]["message"]


def test_ssrf_guard_refuses_internal_hosts(client, monkeypatch):
    from app.config import get_settings
    from app.urls import is_internal_host
    assert all(map(is_internal_host, ["localhost", "127.0.0.1", "10.0.0.5", "169.254.169.254", "mock", "[::1]",
                                      "db.internal"]))
    assert not any(map(is_internal_host, ["lemmy.world", "8.8.8.8", "forum.example.org"]))
    monkeypatch.setattr(get_settings(), "allow_internal_hosts", False)
    job = import_csv(client, "name,url\nmetadata,http://169.254.169.254/latest/meta-data\n", check_reachability=True)
    (row,) = report_rows(client, job)
    assert row["status"] == "invalid" and "private or internal host" in row["errors"]
    d = client.post("/api/v1/destinations", headers=_key(), json={
        "name": f"x {uuid.uuid4().hex[:6]}", "url": f"https://x-{uuid.uuid4().hex[:6]}.example",
        "connector_key": "assisted", "rules_url": "http://10.0.0.5/admin"}).json()
    job = client.post(f"/api/v1/destinations/{d['id']}/rules/capture", headers=_key(), json={}).json()
    err = client.get(f"/api/v1/jobs/{job['job_id']}").json()["error"]
    assert err["code"] == "RULES_PAGE_UNAVAILABLE" and "public host" in err["message"]


def test_cloud_metadata_refused_even_when_internal_hosts_allowed(client):
    """The demo allows internal hosts for its mocks; the VM's credential endpoint must still be unreachable."""
    from app.config import get_settings
    from app.urls import is_metadata_host, url_refused
    assert get_settings().allow_internal_hosts is True  # as on the local stack and the live demo
    assert all(map(is_metadata_host, ["169.254.169.254", "metadata.google.internal", "[fe80::1]"]))
    assert url_refused("169.254.169.254", allow_internal=True) and not url_refused("mock", allow_internal=True)
    job = import_csv(client, "name,url\nmetadata,http://169.254.169.254/latest/meta-data\n", check_reachability=True)
    (row,) = report_rows(client, job)
    assert row["status"] == "invalid" and "private or internal host" in row["errors"]
    d = client.post("/api/v1/destinations", headers=_key(), json={
        "name": f"m {uuid.uuid4().hex[:6]}", "url": f"https://m-{uuid.uuid4().hex[:6]}.example",
        "connector_key": "assisted", "rules_url": "http://169.254.169.254/latest/meta-data/iam"}).json()
    job = client.post(f"/api/v1/destinations/{d['id']}/rules/capture", headers=_key(), json={}).json()
    assert client.get(f"/api/v1/jobs/{job['job_id']}").json()["error"]["code"] == "RULES_PAGE_UNAVAILABLE"


def test_proxy_storage_links_go_through_the_api(monkeypatch):
    from app.config import get_settings
    from app.storage import S3Storage
    s = get_settings().model_copy(update={"storage_links": "proxy", "public_base_url": "https://demo.example"})
    assert S3Storage(s).url_for("imports/x/report.csv") == "https://demo.example/api/v1/files/imports/x/report.csv"


def test_relevance_endpoint(client):
    d = client.post("/api/v1/destinations", headers=_key(), json={
        "name": "Indie Hackers", "url": f"https://indie-{uuid.uuid4().hex[:6]}.example", "connector_key": "assisted",
        "description": "Founders building profitable SaaS products", "est_audience": 90000}).json()
    r = client.post(f"/api/v1/destinations/{d['id']}/relevance", headers=_key(), json={}).json()
    assert r["relevance_score"] >= 4 and "founders" in r["relevance_reason"] and r["provider"]["cost_usd"] == 0


# ------------------------------------------------------------------ Claude adapter, against a fake client (no network)
def _fake_response(stop_reason="end_turn", text='{"score": 4, "reason": "AI video creators"}', model="claude-opus-5-5"):
    return SimpleNamespace(stop_reason=stop_reason, stop_details=SimpleNamespace(category="cyber"), model=model,
                           content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def _provider(response, calls):
    p = AnthropicProvider.__new__(AnthropicProvider)
    import anthropic
    p._anthropic, p.model, p.effort, p.max_tokens = anthropic, "claude-opus-5-5", "low", 4000

    def create(**kwargs):
        calls.append(kwargs)
        return response
    p.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return p


def test_claude_adapter_structured_output_cost_and_fallback():
    calls = []
    res = _provider(_fake_response(), calls).score_relevance({"name": "x"})
    assert res.data == {"score": 4, "reason": "AI video creators"}
    assert res.cost_usd == pytest.approx((1000 * 4 + 200 * 20) / 1e6)
    sent = calls[0]
    assert sent["output_config"]["format"]["type"] == "json_schema" and sent["output_config"]["effort"] == "low"
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    served_by_fallback = _provider(_fake_response(model="claude-opus-5"), []).score_relevance({"name": "x"})
    assert served_by_fallback.warnings == ["AI_FALLBACK_MODEL: served by claude-opus-5"]


def test_claude_adapter_refusal_and_truncation_are_explicit_errors():
    with pytest.raises(AppError) as e:
        _provider(_fake_response(stop_reason="refusal"), []).extract_rules("rules")
    assert e.value.code.value == "AI_REFUSED" and e.value.details["category"] == "cyber"
    with pytest.raises(AppError) as e:
        _provider(_fake_response(stop_reason="max_tokens"), []).extract_rules("rules")
    assert e.value.code.value == "AI_PROVIDER_ERROR" and e.value.retryable
