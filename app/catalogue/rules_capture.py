"""Rules capture: read a community's rules page, let the AI provider propose posting rules, store the proposal.
A proposal changes nothing until a person confirms it (POST /api/v1/rule-proposals/{id}/confirm).

robots.txt is honoured before the page is fetched: no scraping where it is not allowed.
"""
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.ai import get_provider
from app.config import get_settings
from app.db import session_scope
from app.errors import AppError, ErrorCode
from app.jobs import JobContext, handler
from app.models import Destination, RuleProposal, utcnow
from app.output_manifest import build_manifest
from app.storage import get_storage
from app.urls import url_refused

log = logging.getLogger("app.rules_capture")
RULE_FIELDS = ("frequency_cap", "self_promo_allowed", "link_policy", "disclosure_required", "automation_allowed",
               "language")
MAX_TEXT_CHARS = 60_000


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "li", "br", "div", "h1", "h2", "h3", "h4", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", "".join(p.parts))).strip()


def fetch_rules_page(url: str) -> str:
    s = get_settings()
    headers = {"User-Agent": s.crawler_user_agent}
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or url_refused(parts.hostname, s.allow_internal_hosts):
        raise AppError(ErrorCode.RULES_PAGE_UNAVAILABLE, "Rules page must be http(s) on a public host",
                       {"host": parts.hostname})
    with httpx.Client(timeout=10, follow_redirects=True, headers=headers) as client:
        try:
            robots = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
            if robots.status_code == 200:
                rp = RobotFileParser()
                rp.parse(robots.text.splitlines())
                if not rp.can_fetch(s.crawler_user_agent, url):
                    raise AppError(ErrorCode.RULES_PAGE_UNAVAILABLE,
                                   "robots.txt does not allow fetching this rules page; capture the rules by hand",
                                   {"url": url})
            r = client.get(url)
        except httpx.HTTPError as e:
            raise AppError(ErrorCode.RULES_PAGE_UNAVAILABLE, f"Could not fetch the rules page ({type(e).__name__})",
                           {"url": url})
    if r.status_code >= 400:
        raise AppError(ErrorCode.RULES_PAGE_UNAVAILABLE, f"Rules page answered {r.status_code}",
                       {"url": url, "status": r.status_code})
    body = r.content[: s.rules_fetch_max_bytes].decode(r.encoding or "utf-8", errors="replace")
    text = html_to_text(body) if "html" in r.headers.get("content-type", "html") else body
    if len(text) < 40:
        raise AppError(ErrorCode.RULES_PAGE_UNAVAILABLE, "Rules page has almost no text (needs JavaScript?)",
                       {"url": url, "chars": len(text)})
    return text[:MAX_TEXT_CHARS]


@handler("rules_capture")
def run_rules_capture(ctx: JobContext) -> dict:
    dest_id = ctx.payload["destination_id"]
    with session_scope() as db:
        dest = db.get(Destination, dest_id)
        if dest is None:
            raise AppError(ErrorCode.NOT_FOUND, "Destination not found", {"destination_id": dest_id})
        url = ctx.payload.get("rules_url") or dest.rules_url
    if not url:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Destination has no rules_url; pass rules_url in the request")
    text = fetch_rules_page(url)
    ctx.progress(40)
    snapshot_url = get_storage().put_bytes(f"rules/{dest_id}/{ctx.job_id}/page.txt", text.encode(), "text/plain")
    result = get_provider().extract_rules(text)
    proposed = {k: result.data.get(k) for k in RULE_FIELDS}
    with session_scope() as db:
        prop = RuleProposal(destination_id=dest_id, source_url=url, proposed=proposed,
                            evidence=result.data.get("evidence", []), provider=result.manifest_entry("rules_capture"),
                            snapshot_url=snapshot_url, review_note=result.data.get("notes"))
        db.add(prop)
        db.flush()
        prop_id = prop.id
    manifest = build_manifest(job_id=ctx.job_id, kind="rules_capture", providers=[result.manifest_entry("rules")],
                              cost_estimate_usd=result.cost_usd, warnings=result.warnings,
                              ai_generated=result.provider != "mock")
    log.info("rules proposed", extra={"destination_id": dest_id, "proposal_id": prop_id})
    return {"proposal_id": prop_id, "status": "proposed", "destination_id": dest_id, "proposed": proposed,
            "evidence": result.data.get("evidence", []), "notes": result.data.get("notes"),
            "next_step": f"POST /api/v1/rule-proposals/{prop_id}/confirm after review", "manifest": manifest,
            "links": {"page_snapshot": snapshot_url}}


def review(proposal_id: str, decision: str, reviewed_by: str, overrides: dict | None, note: str | None) -> dict:
    with session_scope() as db:
        prop = db.get(RuleProposal, proposal_id)
        if prop is None:
            raise AppError(ErrorCode.NOT_FOUND, "Rule proposal not found")
        if prop.status != "proposed":
            raise AppError(ErrorCode.VALIDATION_ERROR, f"Proposal is already {prop.status}")
        prop.status, prop.reviewed_by, prop.reviewed_at = decision, reviewed_by, utcnow()
        if note:
            prop.review_note = f"{prop.review_note or ''}\nReviewer: {note}".strip()
        if decision == "confirmed":
            unknown = set(overrides or {}) - set(RULE_FIELDS)
            if unknown:
                raise AppError(ErrorCode.VALIDATION_ERROR, f"Unknown rule fields: {sorted(unknown)}")
            applied = {k: v for k, v in {**prop.proposed, **(overrides or {})}.items() if v is not None}
            dest = db.get(Destination, prop.destination_id)
            for k, v in applied.items():
                setattr(dest, k, v)
            prop.applied = applied
        return {"proposal_id": prop.id, "status": prop.status, "destination_id": prop.destination_id,
                "applied": prop.applied, "reviewed_by": reviewed_by}
