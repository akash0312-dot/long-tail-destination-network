"""Deterministic heuristics behind the mock AI provider: rule extraction and relevance scoring.
Same output shape as the Claude provider, so callers never know which one ran."""
import re

AUDIENCE = {
    "AI": ["ai", "artificial intelligence", "machine learning", "ml", "llm", "gpt", "genai", "generative",
           "deep learning", "prompt", "chatbot", "neural"],
    "video": ["video", "youtube", "film", "filmmaking", "editing", "premiere", "davinci", "streaming", "vlog",
              "animation", "motion", "ctv", "shorts", "camera"],
    "marketing": ["marketing", "seo", "growth", "advertising", "content marketing", "social media", "brand",
                  "copywriting", "newsletter", "email marketing", "audience"],
    "SaaS": ["saas", "software", "b2b", "startup tools", "product hunt", "indie hackers", "api", "devtools",
             "developer", "no-code", "nocode", "self-hosted"],
    "creators": ["creator", "creators", "influencer", "podcast", "content creation", "youtuber", "artist",
                 "writer", "blogging", "patreon", "substack"],
    "founders": ["founder", "founders", "startup", "startups", "entrepreneur", "bootstrapped", "indie hacker",
                 "venture", "smallbusiness", "small business", "solopreneur"],
}


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _find(sentences: list[str], pattern: str) -> str | None:
    rx = re.compile(pattern, re.IGNORECASE)
    return next((s for s in sentences if rx.search(s)), None)


WORD_NUM = {"one": 1, "once": 1, "a": 1, "an": 1, "two": 2, "twice": 2, "three": 3, "thrice": 3, "four": 4,
            "five": 5, "ten": 10}
PERIOD_DAYS = {"day": 1, "24 hours": 1, "week": 7, "month": 30, "fortnight": 14}


def extract_rules(text: str) -> dict:
    """Propose posting rules from a rules page. Unknown -> None, so a person fills the gap when confirming."""
    sentences = _sentences(text)
    out = {"frequency_cap": None, "self_promo_allowed": None, "link_policy": None, "disclosure_required": None,
           "automation_allowed": None, "language": None, "evidence": [], "notes": ""}

    def ev(field: str, quote: str | None):
        if quote:
            out["evidence"].append({"field": field, "quote": quote[:300]})
        return quote

    freq = re.compile(r"\b(\d+|one|once|a|an|two|twice|three|thrice|four|five|ten)\s*(?:posts?|threads?|times?|"
                      r"submissions?)?\s*(?:per|a|an|each|every|in a|in any)\s*(day|24 hours|week|fortnight|month)\b",
                      re.IGNORECASE)
    for s in sentences:
        m = freq.search(s)
        if m and re.search(r"post|thread|submi|promot|share|self", s, re.IGNORECASE):
            n = int(m.group(1)) if m.group(1).isdigit() else WORD_NUM[m.group(1).lower()]
            out["frequency_cap"] = {"posts": n, "per_days": PERIOD_DAYS[m.group(2).lower()]}
            ev("frequency_cap", s)
            break

    no_promo = _find(sentences, r"\bno (self[- ]?promotion|promotion|advertis\w*|ads|spam)\b|self[- ]?promotion is "
                                r"(not allowed|prohibited|banned|forbidden)|do not (promote|advertise)|don't "
                                r"(promote|advertise)|9:1|10% rule")
    yes_promo = _find(sentences, r"self[- ]?promotion is (allowed|welcome|ok)|promote your|share your (own )?"
                                 r"(work|project|product)s?|show (and|&) tell|showcase")
    if no_promo:
        out["self_promo_allowed"] = False
        ev("self_promo_allowed", no_promo)
    elif yes_promo:
        out["self_promo_allowed"] = True
        ev("self_promo_allowed", yes_promo)

    no_links = _find(sentences, r"\bno (external )?links\b|links are not (allowed|permitted)|do not (post|include) "
                                r"links|without links")
    max_links = re.search(r"(?:at most|maximum of|max(?:imum)?|no more than|up to)\s+(\d+|one|two|three)\s+links?",
                          text, re.IGNORECASE)
    if no_links:
        out["link_policy"] = {"allowed": False, "max_links": 0}
        ev("link_policy", no_links)
    elif max_links:
        n = max_links.group(1)
        out["link_policy"] = {"allowed": True, "max_links": int(n) if n.isdigit() else WORD_NUM[n.lower()]}
        ev("link_policy", _find(sentences, re.escape(max_links.group(0))))

    disclose = _find(sentences, r"(ai[- ]generated|generated (by|with) ai|chatgpt|llm|ai[- ]assisted|ai content).*"
                                r"(must|should|has to|need to|required).*(label|disclos|tag|flair|mark|state)|"
                                r"(label|disclos|tag|flair|mark).*(ai[- ]generated|ai content|use of ai)")
    ai_ban = _find(sentences, r"no (ai[- ]generated|ai) (content|posts|art)|ai[- ]generated (content|posts) "
                              r"(is|are) (not allowed|banned|prohibited)")
    if disclose:
        out["disclosure_required"] = True
        ev("disclosure_required", disclose)
    if ai_ban:
        out["notes"] += "AI-generated content is banned here: do not post AI content. "
        ev("notes", ai_ban)

    bots = _find(sentences, r"\bno bots\b|bots are not (allowed|permitted)|automated (posting|posts|accounts) "
                            r"(is|are) (not allowed|prohibited|banned)|no automat\w+ (posting|posts)|"
                            r"must be posted by a (human|person)")
    if bots:
        out["automation_allowed"] = False
        ev("automation_allowed", bots)

    lang = re.search(r"(?:posts? (?:must|should) be (?:written )?in|(?:english|german|french|spanish|portuguese|"
                     r"japanese) only|only in)\s*(english|german|deutsch|french|français|spanish|español|portuguese|"
                     r"japanese|日本語)?", text, re.IGNORECASE)
    codes = {"english": "en", "german": "de", "deutsch": "de", "french": "fr", "français": "fr", "spanish": "es",
             "español": "es", "portuguese": "pt", "japanese": "ja", "日本語": "ja"}
    if lang:
        word = (lang.group(1) or lang.group(0).split()[0]).lower()
        if word in codes:
            out["language"] = codes[word]
            ev("language", _find(sentences, re.escape(lang.group(0))))

    found = sum(v is not None for k, v in out.items() if k not in ("evidence", "notes"))
    out["notes"] += f"{found} of 6 rule fields found in the page; unknown fields stay empty for the reviewer."
    return out


def score_relevance(dest: dict) -> dict:
    """1-5 against the audience (AI, video, marketing, SaaS, creators, founders), with the evidence in the reason."""
    text = " ".join(str(dest.get(k) or "") for k in ("name", "category", "description", "url"))
    text += " " + " ".join(dest.get("audience_tags") or [])
    low = re.sub(r"[_/.\-]+", " ", text.lower())
    hits = {seg: [w for w in words if re.search(rf"\b{re.escape(w)}\b", low)] for seg, words in AUDIENCE.items()}
    matched = {seg: ws for seg, ws in hits.items() if ws}
    score = {0: 1, 1: 3, 2: 4}.get(len(matched), 5)
    size = dest.get("est_audience")
    size_note = ""
    if isinstance(size, int):
        if size >= 50_000 and score < 5 and matched:
            score += 1
            size_note = f"; large audience ({size:,})"
        elif size < 200 and score > 1:
            score -= 1
            size_note = f"; very small audience ({size:,})"
    if matched:
        detail = ", ".join(f"{seg} ({', '.join(ws[:2])})" for seg, ws in matched.items())
        reason = f"Matches {len(matched)} audience segment(s): {detail}{size_note}."
    else:
        reason = f"No overlap with AI, video, marketing, SaaS, creator or founder topics{size_note}."
    return {"score": score, "reason": reason}
