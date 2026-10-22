"""The log audit must fail on each kind of violation, and pass on clean logs."""
import json

from scripts.log_audit import DEFAULT_ALLOWED, audit, parse


def line(msg, **kw):
    return "worker-1  | " + json.dumps({"timestamp": kw.pop("t", "2026-10-23T10:00:00+00:00"), "level": "INFO",
                                        "message": msg, **kw})


def run(tmp_path, lines):
    p = tmp_path / "compose.log"
    p.write_text("\n".join(lines), encoding="utf-8")
    return audit(parse(p), p.read_text(encoding="utf-8"), DEFAULT_ALLOWED)


CLEAN = [line("job started", job_id="j1", kind="certification"),
         line("platform call", job_id="j1", op="publish", host="ghost:2368", status=201, connector="ghost"),
         line("platform call", job_id="j1", op="delete", host="ghost:2368", status=204, connector="ghost",
              t="2026-10-23T10:00:02+00:00"),
         line("rate limited, backing off", job_id="j2", op="publish", host="mock:9000", wait_s=1.0,
              reset_source="Retry-After", connector="devto")]


def test_clean_logs_pass(tmp_path):
    r = run(tmp_path, CLEAN)
    assert r["passed"], r["violations"]
    assert r["certification_test_posts"][0]["seconds_to_delete"] == 2.0
    assert r["rate_limit_backoffs"][0]["reset_source"] == "Retry-After"


def test_unknown_host_fails(tmp_path):
    r = run(tmp_path, CLEAN + [line("platform call", job_id="j3", op="publish", host="lemmy.world", status=200)])
    assert not r["passed"] and "lemmy.world" in r["violations"][0]


def test_undeleted_test_post_fails(tmp_path):
    r = run(tmp_path, CLEAN[:2])
    assert not r["passed"] and "not deleted" in r["violations"][0]


def test_secret_in_logs_fails(tmp_path):
    r = run(tmp_path, CLEAN + ["worker-1  | Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"])
    assert not r["passed"] and any("bearer token" in v for v in r["violations"])
    assert run(tmp_path, CLEAN + ["worker-1  | Authorization: Bearer [REDACTED]"])["passed"]
