"""Assisted tier: for communities where automated posting is not allowed. Builds a pack a person can post from in a
minute: caption, media list, a deep link to the community's submit page and a checklist of that community's rules.

destination.params (all optional):
  submit_url_template  e.g. "https://news.example/submit?url={url}&title={title}"  (values are URL-encoded)
  submit_url           a plain submit page when the site takes no prefill parameters
The pack is written to object storage (pack.json + post.md); nothing is posted anywhere.
"""
import hashlib
import json
from urllib.parse import quote

from app.config import get_settings
from app.connectors.base import BaseConnector, violation_error
from app.connectors.schemas import DestinationRef, Rendition
from app.rules import DISCLOSURES, primary_lang, rules_of, stateless_checks
from app.storage import get_storage


def _caption(r: dict) -> str:
    parts = [r.get("title", "").strip(), (r.get("body") or "").strip()]
    if r.get("link_url") and r["link_url"] not in (r.get("body") or ""):
        parts.append(r["link_url"])
    if r.get("tags"):
        parts.append(" ".join(f"#{t.lstrip('#')}" for t in r["tags"]))
    return "\n\n".join(p for p in parts if p)


def _checklist(destination: DestinationRef, r: dict) -> list[str]:
    items = ["Post from the creator's own account, by hand. Do not automate or schedule this post."]
    if destination.rules_url:
        items.append(f"Read the community rules first: {destination.rules_url}")
    cap = destination.frequency_cap
    if cap and cap.get("posts"):
        window = f"{cap['per_days']} day(s)" if cap.get("per_days") else f"{cap.get('per_hours')} hour(s)"
        items.append(f"Post at most {cap['posts']} time(s) per {window} here.")
    if destination.self_promo_allowed is False:
        items.append("Self-promotion is banned: frame it as a question or discussion, not an announcement.")
    policy = destination.link_policy or {}
    if policy.get("allowed") is False:
        items.append("Links are not allowed: remove every URL before posting.")
    elif policy.get("max_links") is not None:
        items.append(f"Use at most {policy['max_links']} link(s).")
    if destination.disclosure_required:
        lang = primary_lang(destination.language) or "en"
        items.append(f"Keep the AI disclosure line: \"{DISCLOSURES.get(lang, DISCLOSURES['en'])}\"")
    if destination.language:
        items.append(f"Write in the community's language ({destination.language}).")
    items.append("After posting, paste the live URL back so the post is tracked.")
    return items


class Connector(BaseConnector):
    def build_assist_pack(self, rendition: Rendition, destination: DestinationRef) -> dict:
        checks, r, warnings = stateless_checks(rendition.model_dump(), rules_of(destination),
                                               get_settings().own_domain_list(), autofix=True)
        failed = [c for c in checks if not c.passed]
        if failed:
            raise violation_error([{"field": "rules", "rule": c.rule, "code": c.code, "message": c.detail}
                                   for c in failed])
        caption = _caption(r)
        params = destination.params or {}
        if params.get("submit_url_template"):
            deep_link = params["submit_url_template"].format(
                url=quote(r.get("link_url") or r.get("canonical_url") or "", safe=""),
                title=quote(r.get("title", ""), safe=""), text=quote(caption[:1500], safe=""))
        else:
            deep_link = params.get("submit_url") or destination.url or ""
        checklist = _checklist(destination, r)
        media = [{"name": f"media_{i + 1}", "url": m["url"], "mime_type": m.get("mime_type"), "kind": "media"}
                 for i, m in enumerate(r.get("media", []))]

        pack_id = hashlib.sha256(json.dumps([destination.id, r], sort_keys=True, default=str).encode()).hexdigest()[:16]
        prefix = f"assist-packs/{destination.id or 'adhoc'}/{pack_id}"
        storage = get_storage()
        post_md = f"# {r.get('title', '')}\n\n{caption}\n\n---\nSubmit here: {deep_link}\n\nChecklist:\n" + \
                  "\n".join(f"- [ ] {c}" for c in checklist)
        files = [{"name": "post.md", "mime_type": "text/markdown", "kind": "caption",
                  "url": storage.put_bytes(f"{prefix}/post.md", post_md.encode(), "text/markdown")}]
        pack = {"caption": caption, "deep_link": deep_link, "checklist": checklist, "media": media,
                "warnings": warnings}
        files.append({"name": "pack.json", "mime_type": "application/json", "kind": "pack",
                      "url": storage.put_bytes(f"{prefix}/pack.json", json.dumps(pack, indent=2).encode(),
                                               "application/json")})
        return {"files": files + media, "caption": caption, "deep_link": deep_link, "checklist": checklist,
                "warnings": warnings}
