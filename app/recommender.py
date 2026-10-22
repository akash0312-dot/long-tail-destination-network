"""Recommender: given one content item, rank destinations by fit, with a reason for each.

Pure and deterministic (no I/O): the API passes destinations + publication history; the offline evaluation passes a
catalogue CSV + a labelled set. Score in 0..1:

  0.50 topic      overlap of audience segments (AI, video, marketing, SaaS, creators, founders) + shared keywords
  0.25 relevance  the destination's relevance score (1-5) against the overall audience
  0.15 past       engagement of earlier posts there (stored metrics); neutral 0.5 when there is no history yet
  0.10 headroom   1 when the frequency cap allows a post now, 0 when the next slot is later
  x 0.8 when the destination is not certified yet

Excluded, with the reason: demoted destinations, content types the connector cannot carry, and rules that would block
the post (self-promotion, links, language). Destinations that ban automation stay in, as assist packs.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from app.catalogue.heuristics import AUDIENCE
from app.rules import frequency_check, stateless_checks

WEIGHTS = {"topic": 0.50, "relevance": 0.25, "past": 0.15, "headroom": 0.10}
UNCERTIFIED_FACTOR = 0.8
STOP = {"the", "and", "for", "with", "your", "you", "our", "that", "this", "from", "about", "into", "how", "what",
        "why", "are", "was", "were", "has", "have", "will", "can", "all", "any", "more", "most", "use", "using",
        "community", "forum", "discussion", "discussions", "share", "sharing", "people", "post", "posts", "news",
        "place", "everything", "related", "general", "topics", "welcome", "official", "free", "best", "new"}
VIDEO_TYPES = {"video", "video_link"}


@dataclass
class Candidate:
    destination: dict
    score: float = 0.0
    components: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    excluded: str | None = None


def _norm(text: str) -> str:
    return re.sub(r"[_/.\-#]+", " ", text.lower())


def segments(text: str) -> dict[str, list[str]]:
    low = _norm(text)
    return {seg: ws for seg, words in AUDIENCE.items()
            if (ws := [w for w in words if re.search(rf"\b{re.escape(w)}\b", low)])}


def keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+]{3,}", _norm(text)) if w not in STOP}


def content_text(c: dict) -> str:
    return " ".join([c.get("title", ""), c.get("body", ""), " ".join(c.get("tags") or [])])


def destination_text(d: dict) -> str:
    tags = d.get("audience_tags") or []
    tags = tags if isinstance(tags, list) else re.split(r"[;,|]", str(tags))
    return " ".join(str(x) for x in [d.get("name", ""), d.get("category") or "", d.get("description") or "",
                                     " ".join(tags), d.get("url", "")])


def past_engagement(history: list[dict]) -> float | None:
    """Average interactions per post at one destination: likes + 2*comments + clicks + views/100."""
    scores = []
    for p in history:
        m = p.get("metrics") or {}
        if m:
            scores.append((m.get("likes") or 0) + 2 * (m.get("comments") or 0) + (m.get("clicks") or 0)
                          + (m.get("views") or 0) / 100)
    return sum(scores) / len(scores) if scores else None


def rank(content: dict, destinations: list[dict], history: dict[str, list[dict]] | None = None,
         now: datetime | None = None, surfaces: dict[str, list[str]] | None = None,
         own_domains: list[str] | None = None, ignore_certification: bool = False) -> list[Candidate]:
    """history: destination id -> its publications ({created_at, status, is_test, metrics}).
    surfaces: connector key -> content types it accepts (from the manifests)."""
    history = history or {}
    c_text = content_text(content)
    c_segs, c_words = segments(c_text), keywords(c_text)
    c_type = content.get("content_type") or "text"
    engagement = {did: past_engagement(h) for did, h in history.items()}
    best_engagement = max([e for e in engagement.values() if e], default=0)
    out = []
    for d in destinations:
        cand = Candidate(d)
        out.append(cand)
        if d.get("certification_status") == "failed" or d.get("enabled") is False:
            cand.excluded = "demoted after a failed certification" if d.get("certification_status") == "failed" \
                else "disabled"
            continue
        accepted = (surfaces or {}).get(d.get("connector_key"))
        if accepted and c_type not in accepted:
            cand.excluded = f"{d.get('connector_key')} cannot carry '{c_type}' content"
            continue
        checks, _, _ = stateless_checks(content, {k: d.get(k) for k in (
            "self_promo_allowed", "link_policy", "disclosure_required", "language")}, own_domains, autofix=True)
        blocked = [c for c in checks if not c.passed]
        if blocked:
            cand.excluded = f"rules: {blocked[0].detail}"
            continue

        d_segs, d_words = segments(destination_text(d)), keywords(destination_text(d))
        shared_segs = sorted(set(c_segs) & set(d_segs))
        seg_score = len(shared_segs) / len(c_segs) if c_segs else 0.0
        shared_words = sorted(c_words & d_words)
        word_score = min(1.0, len(shared_words) / 4)
        topic = 0.7 * seg_score + 0.3 * word_score
        relevance = (d.get("relevance_score") or 3) / 5
        e = engagement.get(d.get("id"))
        past = 0.5 if e is None else (0.5 + 0.5 * e / best_engagement if best_engagement else 0.5)
        recent = [p["created_at"] for p in history.get(d.get("id"), [])
                  if p.get("status") == "published" and not p.get("is_test")]
        cap = d.get("frequency_cap")
        cap_check, next_slot = frequency_check(cap, recent, now) if (cap and now) else (None, None)
        headroom = 0.0 if next_slot else 1.0
        cand.components = {"topic": round(topic, 3), "relevance": round(relevance, 3), "past": round(past, 3),
                           "headroom": headroom}
        score = sum(WEIGHTS[k] * v for k, v in cand.components.items())
        if not ignore_certification and d.get("certification_status") != "certified":
            score *= UNCERTIFIED_FACTOR
            cand.reasons.append("not certified yet")
        cand.score = round(score, 4)

        if shared_segs:
            cand.reasons.insert(0, f"audience match: {', '.join(shared_segs)} "
                                   f"({len(shared_segs)}/{len(c_segs)} of the content's topics)")
        elif shared_words:
            cand.reasons.insert(0, f"shared keywords: {', '.join(shared_words[:4])}")
        else:
            cand.reasons.insert(0, "little topical overlap")
        cand.reasons.append(f"relevance {d.get('relevance_score') or '?'}/5")
        if e is not None:
            cand.reasons.append(f"past posts average {e:.0f} interactions")
        if next_slot:
            cand.reasons.append(f"frequency cap reached; next slot {next_slot:%Y-%m-%d}")
        if d.get("automation_allowed") is False:
            cand.reasons.append("a person posts (assist pack)")
    eligible = sorted((c for c in out if not c.excluded), key=lambda c: (-c.score, c.destination.get("name", "")))
    return eligible + [c for c in out if c.excluded]


def as_dict(c: Candidate) -> dict:
    d = c.destination
    return {"destination_id": d.get("id"), "name": d.get("name"), "url": d.get("url"),
            "connector_key": d.get("connector_key"), "score": c.score, "reason": "; ".join(c.reasons),
            "components": c.components, **({"excluded": c.excluded} if c.excluded else {})}


def precision_at_k(ranked_names: list[str], relevant: set[str], k: int = 10) -> float:
    return sum(1 for n in ranked_names[:k] if n in relevant) / k
