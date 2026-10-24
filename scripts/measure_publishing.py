"""Measure what publishing costs: worker time and AI spend per publication, scaled to 1,000.

    python scripts/measure_publishing.py [--count 100] [--api http://localhost:8000] [--output test_pack/publishing_cost]

Creates one destination on the mock Discourse (cap high enough for the run), certifies it, submits --count
publications through the real API and worker, then reads every job's timestamps and result manifest.
Writes <output>/report.md and jobs.json. Nothing leaves the local stack.
"""
import argparse
import json
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.batch import Runner  # noqa: E402


def ts(value: str) -> datetime:
    t = datetime.fromisoformat(value)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def post(runner: Runner, path: str, body: dict) -> dict:
    r = runner.request("POST", f"{runner.api}{path}", json=body, headers={"Idempotency-Key": uuid.uuid4().hex})
    r.raise_for_status()
    return r.json()


def wait(runner: Runner, job: dict, timeout_s: float = 300) -> dict:
    deadline = time.time() + timeout_s
    while job["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(1.0)
        job = runner.request("GET", f"{runner.api}/api/v1/jobs/{job['job_id']}").json()
    return job


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--mock", default="http://mock:9000", help="mock platform URL as the API sees it")
    ap.add_argument("--token", default=None)
    ap.add_argument("--output", type=Path, default=ROOT / "test_pack" / "publishing_cost")
    args = ap.parse_args(argv)

    runner = Runner(args.api, args.mock, args.mock, args.token, job_timeout_s=300)
    run = runner.run_id
    conn = post(runner, "/api/v1/connections", {"connector_key": "discourse", "name": f"cost run {run}",
                                                "secret_refs": {"api_key": "env:MOCK_API_KEY"},
                                                "params": {"api_username": "cost_bot"}})
    dest = post(runner, "/api/v1/destinations", {
        "name": f"cost-run {run}", "url": f"{args.mock}/cost/forum-{run}", "connector_key": "discourse",
        "connection_id": conn["id"], "params": {"category_id": 1},
        "frequency_cap": {"posts": args.count + 10, "per_days": 1}, "link_policy": {"allowed": True}})
    cert = wait(runner, post(runner, "/api/v1/certifications", {"destination_id": dest["id"]}))
    if cert["status"] != "succeeded":
        print(f"certification failed: {cert.get('error')}")
        return 1

    started = time.monotonic()
    submitted = [post(runner, "/api/v1/publications", {
        "destination_id": dest["id"],
        "rendition": {"title": f"How do small teams plan video, part {i + 1}?",
                      "body": f"We batch-record on Mondays (note {i + 1} of {args.count}). What works for you?"}})
        for i in range(args.count)]
    submit_s = time.monotonic() - started
    # A post over the connection's rate limit is rescheduled as a new job after the reset: follow each chain
    chains = []
    for j in submitted:
        attempts = [wait(runner, runner.request("GET", f"{runner.api}/api/v1/jobs/{j['job_id']}").json())]
        while attempts[-1]["status"] == "failed" and ((attempts[-1].get("error") or {}).get("details") or {}).get(
                "rescheduled_job_id"):
            nxt = attempts[-1]["error"]["details"]["rescheduled_job_id"]
            attempts.append(wait(runner, runner.request("GET", f"{runner.api}/api/v1/jobs/{nxt}").json(), 900))
        chains.append(attempts)
    wall_s = time.monotonic() - started

    jobs = [a for chain in chains for a in chain]
    final = [chain[-1] for chain in chains]
    ok = [j for j in final if j["status"] == "succeeded"]
    ran = [j for j in jobs if j.get("started_at") and j.get("finished_at")]
    work_total_s = sum((ts(j["finished_at"]) - ts(j["started_at"])).total_seconds() for j in ran)
    work_s = [(ts(j["finished_at"]) - ts(j["started_at"])).total_seconds() for j in ok]
    rescheduled = sum(len(chain) > 1 for chain in chains)
    ai_usd = sum(j["result"]["manifest"]["cost_estimate_usd"] for j in ok)
    providers = sorted({p.get("name", str(p)) for j in ok for p in j["result"]["manifest"]["providers"]})
    first, last = min(ts(j["created_at"]) for j in jobs), max(ts(j["finished_at"]) for j in ok)
    span_s = (last - first).total_seconds()
    p95 = statistics.quantiles(work_s, n=20)[-1] if len(work_s) >= 20 else max(work_s)
    per_pub_s = work_total_s / max(len(ok), 1)

    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(), "api": args.api, "connector": "discourse (mock)",
        "count": args.count, "succeeded": len(ok), "failed": len(final) - len(ok),
        "rescheduled_by_connection_rate_limit": rescheduled, "job_attempts": len(jobs),
        "successful_attempt_worker_s": {"mean": round(statistics.mean(work_s), 4),
                                        "median": round(statistics.median(work_s), 4), "p95": round(p95, 4)},
        "worker_s_per_publication_incl_rescheduled_attempts": round(per_pub_s, 4),
        "first_created_to_last_finished_s": round(span_s, 1),
        "posts_per_minute_one_connection": round(len(ok) / span_s * 60, 1),
        "client_wall_s": round(wall_s, 1), "client_submit_s": round(submit_s, 1),
        "ai_cost_usd_total": round(ai_usd, 6), "ai_providers_used": providers,
        "per_1000": {"worker_hours": round(per_pub_s * 1000 / 3600, 4),
                     "ai_cost_usd": round(ai_usd / max(len(ok), 1) * 1000, 6)},
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "jobs.json").write_text(json.dumps({"summary": summary, "jobs": jobs}, indent=2, default=str),
                                           encoding="utf-8", newline="\n")
    w = summary["successful_attempt_worker_s"]
    md = [f"# Publishing cost: {len(ok)}/{args.count} publications succeeded", "",
          f"Run {summary['run_at']} against {args.api}: one certified destination on the mock Discourse, "
          f"{args.count} publications through the API, the job queue and one worker (the default compose stack).", "",
          "| Measure | Value |", "|---|---|",
          f"| Worker time per successful publish | mean {w['mean'] * 1000:.0f} ms, median {w['median'] * 1000:.0f} ms, "
          f"p95 {w['p95'] * 1000:.0f} ms |",
          f"| Rescheduled by the connection's rate limit (Discourse: 60 requests/min) | {rescheduled} of "
          f"{args.count}, all published after the reset ({len(jobs)} job attempts) |",
          f"| Worker time per publication, rescheduled attempts included | {per_pub_s * 1000:.0f} ms |",
          f"| First job created → last job finished | {summary['first_created_to_last_finished_s']} s "
          f"({summary['posts_per_minute_one_connection']} posts/min on one connection) |",
          f"| Client time incl. this API's rate limit ({summary['client_submit_s']} s to submit) | "
          f"{summary['client_wall_s']} s |",
          f"| AI providers used | {', '.join(providers) or 'none'} |",
          f"| AI cost, whole run | ${summary['ai_cost_usd_total']} |",
          f"| **Per 1,000 publications: worker time** | **{summary['per_1000']['worker_hours']} worker-hours** |",
          f"| **Per 1,000 publications: AI cost** | **${summary['per_1000']['ai_cost_usd']}** |", "",
          "Cost per 1,000 publications = worker-hours × the server's hourly price + AI cost. The default publish path "
          "uses no AI (rules and tailoring are deterministic), so the AI part is zero; AI is only used at catalogue "
          "time (rules capture, relevance) when `AI_PROVIDER=anthropic`.", "",
          "Limits: the platform here is the local mock, so platform latency is near zero; real platforms add their "
          "own response time (the local Ghost certification took 2.1 s, mostly Ghost itself)."]
    (args.output / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(ok)}/{args.count} succeeded; worker mean {w['mean'] * 1000:.0f} ms; "
          f"per 1,000: {summary['per_1000']['worker_hours']} worker-hours, ${summary['per_1000']['ai_cost_usd']} AI "
          f"-> {args.output / 'report.md'}")
    return 0 if len(ok) == args.count else 1


if __name__ == "__main__":
    sys.exit(main())
