"""Lightweight language detection for catalogue rows (no model, no network): script ranges, then stop-word votes
for Latin-script languages. Good enough to flag a row whose declared language disagrees with its text."""
import re

SCRIPTS = [("ja", r"[぀-ヿ]"), ("ko", r"[가-힯]"), ("zh", r"[一-鿿]"),
           ("ru", r"[Ѐ-ӿ]"), ("ar", r"[؀-ۿ]"), ("hi", r"[ऀ-ॿ]"),
           ("th", r"[฀-๿]"), ("el", r"[Ͱ-Ͽ]"), ("he", r"[֐-׿]")]
STOPWORDS = {
    "en": {"the", "and", "for", "with", "of", "to", "in", "is", "your", "community", "about"},
    "es": {"el", "la", "los", "las", "de", "para", "con", "y", "en", "comunidad", "sobre"},
    "de": {"der", "die", "das", "und", "für", "mit", "von", "ist", "ein", "eine", "über"},
    "fr": {"le", "la", "les", "des", "et", "pour", "avec", "de", "du", "une", "sur"},
    "pt": {"o", "os", "as", "de", "para", "com", "e", "em", "uma", "do", "sobre"},
    "it": {"il", "lo", "gli", "di", "per", "con", "e", "una", "della", "che", "sul"},
    "nl": {"de", "het", "een", "en", "voor", "met", "van", "op", "over", "is"},
}


def detect(text: str) -> tuple[str | None, float]:
    """Returns (ISO 639-1 code or None, confidence 0-1)."""
    if not text or not text.strip():
        return None, 0.0
    for code, rx in SCRIPTS:
        n = len(re.findall(rx, text))
        if n >= 2:
            # Japanese text also contains kanji; kana decides it is Japanese, not Chinese
            return code, min(1.0, n / max(4, len(text) / 4))
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    if not words:
        return None, 0.0
    votes = {lang: sum(w in sw for w in words) for lang, sw in STOPWORDS.items()}
    best = max(votes, key=votes.get)
    if votes[best] == 0:
        return None, 0.0
    second = sorted(votes.values())[-2]
    return best, round((votes[best] - second) / max(1, votes[best]), 2)
