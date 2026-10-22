"""Export the certification pack: for every certified destination, the run record, the live response captured at
certification time, a screenshot of it, and the deletion confirmation.

    python scripts/export_certification_pack.py [--api http://localhost:8000] [--token T] [--out certification_pack]

Reads only from the running service (API + the signed storage links it returns). Screenshots need Playwright with
Chromium (pip install -r requirements-evidence.txt && python -m playwright install chromium); without it the pack is
still written and the index says screenshots were skipped.
"""
import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

STEP_LABELS = {"publish_test_post": "Published", "confirm_live_url": "Live check", "status_published": "Status",
               "fetch_metrics": "Metrics", "delete_test_post": "Deleted", "status_deleted": "Gone after delete",
               "cleaned_within_limit": "Cleanup time", "build_assist_pack": "Assist pack",
               "pack_files_downloadable": "Pack files"}


DATA_KEY_RE = re.compile(rb'(data-(?:key|api-key|token)=")[^"]*(")')


def redact_snapshot(content: bytes) -> bytes:
    """Captured pages can embed credentials (Ghost puts its public Content API key in data-key): never commit them."""
    content = DATA_KEY_RE.sub(rb"\1[REDACTED]\2", content)
    try:
        from app.logging_setup import redact  # JWTs, Bearer/Ghost tokens, password=..., api_key: ...
        return redact(content.decode("utf-8")).encode("utf-8")
    except (ImportError, UnicodeDecodeError):
        return content


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def latest_runs(client: httpx.Client, api: str, include_batch: bool) -> list[tuple[dict, dict]]:
    dests = client.get(f"{api}/api/v1/destinations").json()["items"]
    out = []
    for d in dests:
        if d["certification_status"] != "certified":
            continue
        if d["name"].startswith("batch-") and not include_batch:
            continue  # temporary destinations created by batch scenarios, not part of the catalogue
        runs = client.get(f"{api}/api/v1/certifications", params={"destination_id": d["id"]}).json()["items"]
        passed = [r for r in runs if r["passed"]]
        if passed:
            out.append((d, passed[0]))  # newest first
    return out


def step(run: dict, name: str) -> dict | None:
    return next((s for s in run["steps"] if s["step"] == name), None)


def render_page(snapshot: bytes, content_type: str, title: str) -> str:
    """HTML to screenshot: the captured page itself, or a readable wrapper for JSON / markdown."""
    if "html" in content_type:
        return snapshot.decode("utf-8", "replace")
    body = snapshot.decode("utf-8", "replace")
    if "json" in content_type:
        try:
            body = json.dumps(json.loads(body), indent=2)
        except ValueError:
            pass
    return (f"<html><body style='font-family:sans-serif'><h3>{html.escape(title)}</h3>"
            f"<p style='color:#555'>Captured response ({html.escape(content_type)})</p>"
            f"<pre style='white-space:pre-wrap;font-size:13px'>{html.escape(body)}</pre></body></html>")


def screenshot(pages: list[tuple[Path, Path]]) -> str:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "skipped (Playwright not installed)"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except PlaywrightError:  # headless shell missing: the full Chromium build also runs headless
                browser = p.chromium.launch(channel="chromium")
            page = browser.new_page(viewport={"width": 1100, "height": 800})
            for html_file, png in pages:
                page.goto(html_file.resolve().as_uri(), wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(300)
                page.screenshot(path=str(png), full_page=True)
            browser.close()
    except PlaywrightError as e:
        return f"skipped (browser could not start: {str(e).splitlines()[0][:120]})"
    return f"{len(pages)} rendered with Chromium (headless) from the captured responses"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--token", default=None)
    ap.add_argument("--out", type=Path, default=Path("certification_pack"))
    ap.add_argument("--include-batch", action="store_true", help="also export batch-scenario destinations")
    args = ap.parse_args()
    client = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {args.token}"} if args.token else {})
    args.out.mkdir(parents=True, exist_ok=True)
    rows, pages = [], []
    for dest, run in latest_runs(client, args.api, args.include_batch):
        folder = args.out / f"{dest['connector_key']}--{slug(dest['name'])}"
        folder.mkdir(parents=True, exist_ok=True)
        record = client.get(run["evidence_url"]).json()
        (folder / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8", newline="\n")
        job = client.get(f"{args.api}/api/v1/jobs/{run['job_id']}").json()
        links = (job.get("result") or {}).get("links", {})
        captured, ctype = None, "text/html"
        if links.get("live_snapshot"):
            r = client.get(links["live_snapshot"])
            captured, ctype = redact_snapshot(r.content), r.headers.get("content-type", "text/html")
            ext = "json" if "json" in ctype else "html"
            (folder / f"live_snapshot.{ext}").write_bytes(captured)
        elif step(record, "build_assist_pack"):  # assisted tier: the pack a person posts from
            files = step(record, "build_assist_pack")["result"]["files"]
            md = next((f for f in files if f["name"] == "post.md"), None)
            if md:
                captured, ctype = client.get(md["url"]).content, "text/markdown"
                (folder / "assist_pack_post.md").write_bytes(captured)
        if captured is not None:
            page_html = folder / "_screenshot_source.html"
            page_html.write_text(render_page(captured, ctype, dest["name"]), encoding="utf-8")
            pages.append((page_html, folder / "screenshot.png"))
        pub = (step(record, "publish_test_post") or {}).get("result", {})
        live = (step(record, "confirm_live_url") or {}).get("result", {})
        deleted = (step(record, "delete_test_post") or {}).get("result")
        gone = (step(record, "status_deleted") or {}).get("result", {})
        cleanup = (step(record, "cleaned_within_limit") or {}).get("result", {})
        summary = {"destination": dest["name"], "destination_id": dest["id"], "connector": dest["connector_key"],
                   "level": dest["level"], "certified_at": dest["last_certified_at"], "run_id": run["job_id"],
                   "duration_s": record["duration_s"], "platform_post_id": pub.get("platform_post_id"),
                   "live_url": pub.get("live_url"), "live_http_status": live.get("status"),
                   "live_content_type": live.get("content_type"), "deleted": deleted,
                   "status_after_delete": gone.get("status"), "cleanup_seconds": cleanup.get("seconds"),
                   "steps": [{"step": s["step"], "ok": s["ok"]} for s in record["steps"]]}
        (folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="\n")
        rows.append((folder.name, summary))
    shots = screenshot(pages) if pages else "none to render"
    for html_file, _ in pages:
        html_file.unlink()
    write_index(args.out, rows, shots, args.api)
    print(f"{len(rows)} certified destinations exported -> {args.out} (screenshots: {shots})")
    return 0 if rows else 1


def write_index(out: Path, rows, shots: str, api: str) -> None:
    lines = ["# Certification pack", "",
             "Every certified destination: the run record (`run.json`, every step with its result), the live "
             "response captured at certification time (`live_snapshot.*`), a screenshot of it (`screenshot.png`), "
             "and the deletion confirmation (the `delete_test_post` and `status_deleted` steps). Exported by "
             f"`scripts/export_certification_pack.py` from {api}. Screenshots: {shots}. They render the response "
             "exactly as captured, offline, so stylesheets served from internal hosts are not loaded (pages look "
             "unstyled); ActivityPub and other JSON responses are shown as formatted text.", "",
             "| Destination | Connector | Level | Test post | Live check | Deleted | Status after delete | Cleanup | "
             "Run time |", "|---|---|---|---|---|---|---|---|---|"]
    for folder, s in sorted(rows, key=lambda r: r[1]["connector"]):
        deleted = "yes" if (s["deleted"] or {}).get("deleted") else ("n/a (person posts)" if s["deleted"] is None
                                                                     else "no")
        live = f"HTTP {s['live_http_status']}" if s["live_http_status"] else "n/a"
        cleanup = f"{s['cleanup_seconds']} s" if s["cleanup_seconds"] is not None else "n/a"
        lines.append(f"| [{s['destination']}]({folder}/) | {s['connector']} | {s['level']} | "
                     f"{s['platform_post_id'] or 'n/a'} | {live} | {deleted} | {s['status_after_delete'] or 'n/a'} | "
                     f"{cleanup} | {s['duration_s']} s |")
    lines += ["", "Levels: L1 assisted (a person posts), L2 feed, L3 native API (publish, status, delete tested), "
              "L4 native + metrics. Ghost and Lemmy run as real local instances; the others run on the mock "
              "platform server (see REPORT.md)."]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    sys.exit(main())
