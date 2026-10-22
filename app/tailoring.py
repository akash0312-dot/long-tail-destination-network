"""Tailoring: turn one content item into a rendition that fits one destination. Deterministic, so the same input
always gives the same output and every change is listed in `changes`.

  forums (Discourse, Lemmy)        discussion-first: question title, CTA lines dropped, closing question, link last,
                                   inline #hashtags moved to tags
  articles (Ghost, Dev.to, Hashnode) canonical URL kept pointing at the original, tags in the platform's format
  video (PeerTube, MRSS)           short title, tags capped
  every destination                word-boundary truncation to the platform's limits, @mention style per platform,
                                   language mismatch flagged (translation is not automatic)
"""
import re

from app.rules import PROMO_PHRASES, primary_lang

FORUM = {"discourse", "lemmy"}
ARTICLE = {"ghost", "devto", "hashnode"}
VIDEO = {"peertube", "mrss"}
FEDIVERSE = {"lemmy", "peertube"}
QUESTION_WORDS = ("how", "why", "what", "which", "who", "when", "where", "should", "is", "are", "do", "does", "can")
HASHTAG_RE = re.compile(r"(?<![\w/])#([A-Za-z][\w-]{1,49})")
ELLIPSIS = "…"


def truncate_words(text: str, limit: int) -> str:
    """Cut at the last word boundary that fits, then add an ellipsis. Never cuts inside a word."""
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - len(ELLIPSIS))]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:.-—") + ELLIPSIS


def discussion_title(title: str) -> str:
    core = title.strip().rstrip(".!")
    if core.endswith("?"):
        return core
    if core.split(" ", 1)[0].lower() in QUESTION_WORDS:
        return core + "?"
    return f"{core}: what has worked for you?"


def _drop_cta_lines(body: str) -> tuple[str, int]:
    kept, dropped = [], 0
    for line in body.splitlines():
        low = line.lower()
        if any(re.search(rf"\b{re.escape(p)}\b", low) for p in PROMO_PHRASES):
            dropped += 1
            continue
        kept.append(line)
    return "\n".join(kept).strip(), dropped


def platform_tags(connector: str, tags: list[str], limit: int | None) -> list[str]:
    clean = [t.lstrip("#").strip() for t in tags if t.lstrip("#").strip()]
    if connector == "devto":
        clean = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in clean]
    elif connector in ("discourse", "hashnode", "lemmy"):
        clean = [re.sub(r"[^a-z0-9-]+", "-", t.lower()).strip("-") for t in clean]
    out = []
    for t in clean:
        if t and t not in out:
            out.append(t)
    return out[:limit] if limit else out


def mention_style(connector: str, mentions: list[str]) -> tuple[list[str], list[str]]:
    """Fediverse platforms need @user@instance; others use @user. Returns (mentions, dropped)."""
    out, dropped = [], []
    for m in mentions:
        handle = m.lstrip("@")
        if connector in FEDIVERSE:
            (out if "@" in handle else dropped).append(f"@{handle}")
        else:
            out.append(f"@{handle.split('@', 1)[0]}")
    return out, dropped


def tailor(content: dict, destination, connector_key: str, surface) -> tuple[dict, list[str]]:
    """content: rendition-shaped dict. destination: Destination row or DestinationRef. surface: manifest Surface."""
    r = {**content}
    spec = surface.spec
    changes: list[str] = []
    body = r.get("body") or ""

    if connector_key in FORUM or (getattr(destination, "category", None) in ("forum", "community")):
        new_title = discussion_title(r.get("title", ""))
        if new_title != r.get("title"):
            changes.append("title reframed as a discussion question")
            r["title"] = new_title
        body, dropped = _drop_cta_lines(body)
        if dropped:
            changes.append(f"removed {dropped} call-to-action line(s)")
        inline = HASHTAG_RE.findall(body)
        if inline:
            body = HASHTAG_RE.sub(lambda m: m.group(1), body)
            r["tags"] = list(r.get("tags") or []) + inline
            changes.append("inline hashtags moved to tags")
        link = r.get("link_url")
        if link and link in body:
            body = body.replace(link, "").strip()
        if link:
            body = f"{body}\n\nSource: {link}".strip()
            changes.append("link placed at the end as the source")
        if "?" not in body:
            body = f"{body}\n\nWhat has worked for you?".strip()
            changes.append("closing question added")

    if connector_key in ARTICLE or getattr(destination, "category", None) in ("blog", "developer_blog"):
        original = r.get("canonical_url") or r.get("link_url")
        if original and r.get("canonical_url") != original:
            r["canonical_url"] = original
            changes.append("canonical URL set to the original article")

    if r.get("tags"):
        tags = platform_tags(connector_key, r["tags"], spec.max_tags)
        if tags != r["tags"]:
            changes.append(f"tags adapted for {connector_key} ({len(tags)} kept)")
        r["tags"] = tags
    if r.get("mentions"):
        mentions, dropped = mention_style(connector_key, r["mentions"])
        if dropped:
            changes.append(f"dropped mentions without an instance: {', '.join(dropped)}")
        if mentions != r["mentions"]:
            changes.append("mentions formatted for the platform")
        r["mentions"] = mentions

    if spec.title_max_chars and len(r.get("title", "")) > spec.title_max_chars:
        r["title"] = truncate_words(r["title"], spec.title_max_chars)
        changes.append(f"title truncated at a word boundary to {spec.title_max_chars} characters")
    if spec.body_max_chars and len(body) > spec.body_max_chars:
        body = truncate_words(body, spec.body_max_chars)
        changes.append(f"body truncated at a word boundary to {spec.body_max_chars} characters")
    r["body"] = body

    dest_lang, content_lang = primary_lang(getattr(destination, "language", None)), primary_lang(r.get("language"))
    if dest_lang and content_lang and dest_lang != content_lang:
        changes.append(f"NEEDS_TRANSLATION: destination language is '{dest_lang}', content is '{content_lang}'")
    return r, changes
