"""Batch runner: run a folder (or file) of scenario inputs against the API and write every result to one folder.

    python -m app.batch --input test_inputs --output outputs/run1 [--api http://localhost:8000] [--token T]

A scenario file is JSON:
  {"name": "...", "description": "...",
   "steps": [{"method": "POST", "path": "/api/v1/publications", "body": {...}, "save_as": "pub",
              "idempotency_key": "optional, default random", "wait_job": true}, ...],
   "expect": [{"step": "pub", "path": "status", "equals": "failed"},
              {"step": "pub", "path": "error.code", "equals": "DEST_RULE_SELF_PROMO"},
              {"step": "pub", "path": "result.warnings", "contains": "AI_DISCLOSURE_ADDED"}]}
Placeholders in paths and bodies: {uuid}, {mock} (mock platform URL as the API sees it), {mock_host} (as this
machine sees it, for "url" steps such as arming faults), {dest:<destination name>} (id by name),
{<save_as>.<dotted.path>} (a value from an earlier step's response).
Writes <output>/<scenario>/steps.json and <output>/summary.json + summary.md (exit code 1 if any check fails).
"""
import argparse
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

PLACEHOLDER = re.compile(r"\{([a-zA-Z_][\w.:\- ()!]*)\}")


def dig(obj, path: str):
    for part in path.split("."):
        if isinstance(obj, list) and part.isdigit():
            obj = obj[int(part)] if int(part) < len(obj) else None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


class Runner:
    def __init__(self, api: str, mock: str, mock_host: str, token: str | None, job_timeout_s: float,
                 verify_tls: bool = True):
        self.api, self.job_timeout_s = api.rstrip("/"), job_timeout_s
        self.mock, self.mock_host = mock.rstrip("/"), mock_host.rstrip("/")
        self.client = httpx.Client(timeout=60, headers={"Authorization": f"Bearer {token}"} if token else {},
                                   verify=verify_tls)
        self._dest_ids: dict[str, str] | None = None
        self.run_id = uuid.uuid4().hex[:8]  # {run_id}: same value for the whole run (unique names, fixed keys)

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        """Be a good client of a rate-limited API: on 429 wait for Retry-After, then retry (max 5 times)."""
        for _ in range(5):
            r = self.client.request(method, url, **kw)
            if r.status_code != 429:
                return r
            time.sleep(min(60.0, float(r.headers.get("Retry-After", "5"))))
        return r

    def dest_id(self, name: str) -> str:
        if self._dest_ids is None or name not in self._dest_ids:  # may have been created by an earlier step
            items = self.request("GET", f"{self.api}/api/v1/destinations").json()["items"]
            self._dest_ids = {d["name"]: d["id"] for d in items}
        if name not in self._dest_ids:
            raise ValueError(f"no destination named {name!r}")
        return self._dest_ids[name]

    def fill(self, value, saved: dict):
        if isinstance(value, dict):
            return {k: self.fill(v, saved) for k, v in value.items()}
        if isinstance(value, list):
            return [self.fill(v, saved) for v in value]
        if not isinstance(value, str):
            return value

        def sub(m):
            token = m.group(1)
            if token == "uuid":
                return uuid.uuid4().hex
            if token == "run_id":
                return self.run_id
            if token == "mock":
                return self.mock
            if token == "mock_host":
                return self.mock_host
            if token.startswith("dest:"):
                return self.dest_id(token[5:])
            head, _, rest = token.partition(".")
            if head in saved:
                return str(dig(saved[head], rest) if rest else saved[head])
            return m.group(0)
        return PLACEHOLDER.sub(sub, value)

    def wait_job(self, job: dict) -> dict:
        deadline = time.time() + self.job_timeout_s
        while job.get("status") in ("queued", "running") and time.time() < deadline:
            if job.get("run_after"):  # scheduled for later on purpose (reschedule): report it as is
                return job
            time.sleep(1.0)
            job = self.request("GET", f"{self.api}/api/v1/jobs/{job['job_id']}").json()
        return job

    def setup_steps(self, setup: dict) -> list[dict]:
        """Shorthand: a fresh connection + destination (+ certification) for this scenario, saved as `dest`."""
        steps = []
        if setup.get("connection"):
            steps.append({"method": "POST", "path": "/api/v1/connections", "body": setup["connection"],
                          "save_as": "conn"})
            setup["destination"] = {**setup["destination"], "connection_id": "{conn.id}"}
        steps.append({"method": "POST", "path": "/api/v1/destinations", "body": setup["destination"],
                      "save_as": "dest"})
        if setup.get("certify", True):
            steps.append({"method": "POST", "path": "/api/v1/certifications",
                          "body": {"destination_id": "{dest.id}"}, "save_as": "setup_cert"})
        return steps

    def run_scenario(self, scenario: dict) -> dict:
        saved: dict = {}
        steps_out = []
        all_steps = self.setup_steps(dict(scenario["setup"])) if scenario.get("setup") else []
        for i, raw in enumerate(all_steps + scenario["steps"]):
            step = self.fill(raw, saved)
            url = step.get("url") or f"{self.api}{step['path']}"
            headers = dict(step.get("headers", {}))
            if step.get("method", "GET").upper() == "POST" and url.startswith(f"{self.api}/api/v1/"):
                headers.setdefault("Idempotency-Key", step.get("idempotency_key") or uuid.uuid4().hex)
            started = time.monotonic()
            payload = {"data": step["form"]} if "form" in step else {"json": step.get("body")}
            r = self.request(step.get("method", "GET"), url, headers=headers, **payload)
            try:
                body = r.json()
            except ValueError:
                body = {"raw": r.text[:2000]}
            if step.get("wait_job", True) and isinstance(body, dict) and "job_id" in body:
                body = self.wait_job(body)
            if step.get("sleep_s"):
                time.sleep(step["sleep_s"])
            name = step.get("save_as") or f"step{i + 1}"
            saved[name] = body
            steps_out.append({"name": name, "request": {"method": step.get("method", "GET"), "url": url,
                                                        "body": step.get("body"),
                                                        "idempotency_key": headers.get("Idempotency-Key")},
                              "http_status": r.status_code, "response": body,
                              "ms": round((time.monotonic() - started) * 1000)})
        checks = []
        for exp in scenario.get("expect", []):
            exp = self.fill(exp, saved)
            actual = dig(saved.get(exp["step"]), exp["path"])
            if "equals" in exp:
                ok = actual == exp["equals"]
            elif "contains" in exp:
                ok = any(exp["contains"] in str(x) for x in (actual if isinstance(actual, list) else [actual]))
            else:
                ok = actual not in (None, "", [], {})
            checks.append({**exp, "actual": actual, "ok": ok})
        return {"name": scenario["name"], "description": scenario.get("description", ""), "steps": steps_out,
                "checks": checks, "passed": all(c["ok"] for c in checks)}


def load_inputs(path: Path) -> list[dict]:
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    scenarios = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for s in (data if isinstance(data, list) else [data]):
            s.setdefault("name", f.stem)
            scenarios.append(s)
    return scenarios


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--mock", default="http://mock:9000", help="mock platform URL as the API sees it ({mock})")
    ap.add_argument("--mock-host", default="http://localhost:9000",
                    help="mock platform URL as this machine sees it ({mock_host}, e.g. to arm faults)")
    ap.add_argument("--token", default=None)
    ap.add_argument("--job-timeout", type=float, default=120)
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (only for a local rehearsal with a self-signed certificate)")
    args = ap.parse_args(argv)

    runner = Runner(args.api, args.mock, args.mock_host, args.token, args.job_timeout, verify_tls=not args.insecure)
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for scenario in load_inputs(args.input):
        res = runner.run_scenario(scenario)
        folder = args.output / re.sub(r"[^\w.-]+", "_", res["name"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "steps.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8", newline="\n")
        results.append(res)
        print(f"{'PASS' if res['passed'] else 'FAIL'}  {res['name']}")
    summary = {"run_at": datetime.now(timezone.utc).isoformat(), "api": args.api,
               "passed": sum(r["passed"] for r in results), "total": len(results),
               "scenarios": [{"name": r["name"], "passed": r["passed"],
                              "failed_checks": [c for c in r["checks"] if not c["ok"]]} for r in results]}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8",
                                              newline="\n")
    md = [f"# Batch run {summary['run_at']}", "", f"**{summary['passed']}/{summary['total']} scenarios passed**", "",
          "| Scenario | Result | Description |", "|---|---|---|"]
    md += [f"| {r['name']} | {'PASS' if r['passed'] else 'FAIL'} | {r['description']} |" for r in results]
    (args.output / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    print(f"{summary['passed']}/{summary['total']} passed -> {args.output}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
