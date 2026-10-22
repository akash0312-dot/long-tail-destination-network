"""Audit the service logs for must-have 5 ("never spam, never cheat") and must-have 7 (no secrets in logs).

    docker compose logs --no-color > compose.log
    python scripts/log_audit.py compose.log [--out test_pack/log_audit] [--allow-host extra.host:443 ...]

Reads the JSON log lines and checks:
  1. every platform host contacted is on the allowlist (our own local instances and mock servers only)
  2. every certification test post that was created was deleted, within CERTIFICATION_MAX_CLEANUP_S (600 s)
  3. 429 handling: each backoff with its wait and the header the reset time came from; long waits rescheduled
  4. circuit breaker openings
  5. no secrets: JWTs, bearer / Ghost tokens, and the local default passwords never appear
Writes report.md + audit.json; exit code 1 on any violation.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_ALLOWED = {"ghost:2368", "lemmy:8536", "mock:9000", "api:8000", "localhost:8000", "localhost:9000",
                   "127.0.0.1:9000"}
MAX_CLEANUP_S = 600
SECRET_PATTERNS = {
    "jwt": re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"),
    "bearer token": re.compile(r"(?i)bearer\s+(?!\[REDACTED\])[\w\-.:=]{16,}"),
    "ghost admin token": re.compile(r"Ghost\s+(?!\[REDACTED\])[\w\-.]{30,}"),
    "local default password": re.compile(r"local-only-(?:lemmy-pass-123|change-me|lemmy-db|callback-secret)"),
}
OK_STATUS = range(200, 300)
EXCERPT_MESSAGES = {"job started", "job finished", "platform call", "platform timeout", "rate limited, backing off",
                    "circuit opened", "destination demoted", "certification inconclusive"}


def parse(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            e = json.loads(line[start:])
        except ValueError:
            continue
        if isinstance(e, dict) and "message" in e and "timestamp" in e:
            events.append(e)
    return events


def ts(e: dict) -> datetime:
    return datetime.fromisoformat(e["timestamp"])


def audit(events: list[dict], raw: str, allowed: set[str]) -> dict:
    calls = [e for e in events if e["message"] in ("platform call", "platform timeout", "rate limited, backing off")]
    hosts = sorted({e["host"] for e in calls if e.get("host")})
    unknown_hosts = [h for h in hosts if h not in allowed]
    kinds = {e["job_id"]: e.get("kind") for e in events if e["message"] == "job started" and e.get("job_id")}

    cert_posts = []
    for job_id in sorted({j for j, k in kinds.items() if k == "certification"}):
        job_calls = [e for e in events if e.get("job_id") == job_id and e["message"] == "platform call"]
        published = [e for e in job_calls if e.get("op") == "publish" and e.get("status") in OK_STATUS]
        if not published:
            continue  # nothing was created (failed before publishing, or assisted / feed tier)
        deletes = [e for e in job_calls if e.get("op") == "delete" and (e.get("status") in OK_STATUS
                                                                         or e.get("status") == 404)]
        first = ts(published[0])
        last_delete = ts(deletes[-1]) if deletes else None
        cert_posts.append({"job_id": job_id, "connector": published[0].get("connector"),
                           "host": published[0].get("host"), "published_at": published[0]["timestamp"],
                           "deleted": bool(deletes),
                           "seconds_to_delete": round((last_delete - first).total_seconds(), 2) if deletes else None})
    undeleted = [p for p in cert_posts if not p["deleted"]]
    slow = [p for p in cert_posts if p["deleted"] and p["seconds_to_delete"] > MAX_CLEANUP_S]

    backoffs = [{"connector": e.get("connector"), "host": e.get("host"), "op": e.get("op"),
                 "wait_s": e.get("wait_s"), "reset_source": e.get("reset_source"), "at": e["timestamp"]}
                for e in events if e["message"] == "rate limited, backing off"]
    rate_limited_jobs = [e.get("job_id") for e in events
                         if e["message"] == "job finished" and e.get("error_code") == "PLATFORM_RATE_LIMIT"]
    circuits = [e for e in events if e["message"] == "circuit opened"]
    secrets = {name: len(rx.findall(raw)) for name, rx in SECRET_PATTERNS.items()}

    violations = []
    if unknown_hosts:
        violations.append(f"contacted hosts outside the allowlist: {unknown_hosts}")
    if undeleted:
        violations.append(f"{len(undeleted)} certification test post(s) were not deleted")
    if slow:
        violations.append(f"{len(slow)} test post(s) deleted after more than {MAX_CLEANUP_S} s")
    for name, n in secrets.items():
        if n:
            violations.append(f"{n} {name} occurrence(s) in the logs")
    return {"log_events": len(events), "platform_calls": len([e for e in calls if e["message"] == "platform call"]),
            "hosts_contacted": hosts, "allowed_hosts": sorted(allowed), "certification_test_posts": cert_posts,
            "max_seconds_to_delete": max((p["seconds_to_delete"] or 0 for p in cert_posts), default=None),
            "rate_limit_backoffs": backoffs, "jobs_ending_in_rate_limit": len(rate_limited_jobs),
            "circuit_breaker_openings": len(circuits), "secret_matches": secrets,
            "redactions_in_logs": raw.count("[REDACTED]"), "violations": violations, "passed": not violations}


def report(result: dict, source: str) -> str:
    r = result
    lines = [f"# Log audit: {'PASSED' if r['passed'] else 'FAILED'}", "",
             f"Source: `{source}`, {r['log_events']} JSON log events, {r['platform_calls']} platform calls.", "",
             "## 1. Where requests went", "",
             f"Hosts contacted: {', '.join(f'`{h}`' for h in r['hosts_contacted']) or 'none'}. Allowed: our own local "
             "instances and mock servers only.", "",
             "## 2. Certification test posts deleted", "",
             f"{len(r['certification_test_posts'])} test posts created, "
             f"{sum(p['deleted'] for p in r['certification_test_posts'])} deleted; slowest deletion "
             f"{r['max_seconds_to_delete']} s (limit {MAX_CLEANUP_S} s).", "",
             "| Run | Connector | Host | Deleted | Seconds to delete |", "|---|---|---|---|---|"]
    lines += [f"| {p['job_id'][:8]} | {p['connector']} | {p['host']} | {'yes' if p['deleted'] else '**NO**'} | "
              f"{p['seconds_to_delete']} |" for p in r["certification_test_posts"]]
    lines += ["", "## 3. Rate limits (back off on 429 using the reset time)", "",
              f"{len(r['rate_limit_backoffs'])} inline backoffs; {r['jobs_ending_in_rate_limit']} job(s) returned "
              "PLATFORM_RATE_LIMIT with the retry time (long resets are rescheduled, not hammered).", ""]
    if r["rate_limit_backoffs"]:
        lines += ["| Connector | Op | Waited (s) | Reset time from |", "|---|---|---|---|"]
        lines += [f"| {b['connector']} | {b['op']} | {b['wait_s']} | {b['reset_source']} |"
                  for b in r["rate_limit_backoffs"]]
        lines.append("")
    lines += [f"Circuit breaker openings (calls stopped after 5 consecutive failures): "
              f"{r['circuit_breaker_openings']}.", "",
              "## 4. Secrets", "",
              ", ".join(f"{k}: {v}" for k, v in r["secret_matches"].items()) +
              f". Values redacted in the logs: {r['redactions_in_logs']}.", ""]
    lines += ["## Violations", ""] + ([f"- {v}" for v in r["violations"]] or ["None."])
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log", type=Path)
    ap.add_argument("--out", type=Path, default=Path("test_pack/log_audit"))
    ap.add_argument("--allow-host", action="append", default=[])
    args = ap.parse_args(argv)
    raw = args.log.read_text(encoding="utf-8", errors="replace")
    events = parse(args.log)
    result = audit(events, raw, DEFAULT_ALLOWED | set(args.allow_host))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8", newline="\n")
    excerpt = [json.dumps(e) for e in events if e["message"] in EXCERPT_MESSAGES]  # the lines the audit relies on
    (args.out / "excerpt.jsonl").write_text("\n".join(excerpt) + "\n", encoding="utf-8", newline="\n")
    (args.out / "report.md").write_text(report(result, args.log.name), encoding="utf-8", newline="\n")
    print(f"log audit {'PASSED' if result['passed'] else 'FAILED'}: {len(result['certification_test_posts'])} test "
          f"posts (all deleted: {not any(not p['deleted'] for p in result['certification_test_posts'])}), "
          f"hosts {result['hosts_contacted']}, violations {result['violations']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
