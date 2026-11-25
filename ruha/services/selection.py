"""Choosing reels: settings, the "Message score", categories, the "find a moment about ..." match and the
pick of the best clips that do not overlap.

The LLM proposes about twice as many candidates as needed and scores each one on five things worth 20 points
each (strong opening line, one clear complete idea, scripture or quotable line, emotional moment, works without
context). The parts are added up here, so the score is a sum of visible judgements rather than one number the model
made up. A candidate the model did not score gets an *estimated* score from the text itself (marked as such).
"""
from __future__ import annotations

import re

from ruha.services import keywords

DEFAULT_COUNT, DEFAULT_MIN_S, DEFAULT_MAX_S = 6, 30.0, 60.0
MAX_COUNT = 12                       # also the most reels one typed list may hold
CATEGORIES = ["Scripture", "Encouragement", "Story", "Teaching", "Prayer"]
CRITERIA = {"opening": "strong opening line (a hook)", "idea": "one clear, complete idea",
            "quotable": "scripture or a quotable line", "emotion": "an emotional moment",
            "standalone": "works without any context"}
PART_MAX = 20
SELECTION_V = 3                       # bump when plans must be redone (stored plans without scores are rebuilt)


# ------------------------------------------------------------------ settings
def reel_settings(cfg: dict | None) -> dict:
    """Number of short reels and their length range from the settings, clamped to sane values."""
    cfg = cfg or {}

    def num(key, default):
        v = cfg.get(key, default)
        return float(default) if isinstance(v, bool) or not isinstance(v, (int, float)) else float(v)
    count = int(max(1, min(MAX_COUNT, num("reels.count", DEFAULT_COUNT))))
    lo = max(5.0, min(num("reels.min_s", DEFAULT_MIN_S), 170.0))
    hi = max(lo + 5.0, min(num("reels.max_s", DEFAULT_MAX_S), 180.0))
    return {"count": count, "min_s": lo, "max_s": hi}


def candidates_wanted(duration: float, rs: dict) -> int:
    """Twice as many candidates as reels needed - but never more than a video of this length can offer."""
    fits = max(2, int(duration // rs["min_s"]))
    return int(min(2 * rs["count"], max(4, 2 * fits), 2 * MAX_COUNT))


# ------------------------------------------------------------------ text signals
_BOOKS = ("genesis|exodus|leviticus|numbers|deuteronomy|joshua|judges|ruth|samuel|kings|chronicles|ezra|nehemiah|esther|job|"
          "psalms?|proverbs|ecclesiastes|isaiah|jeremiah|lamentations|ezekiel|daniel|hosea|joel|amos|micah|habakkuk|"
          "matthew|mark|luke|john|acts|romans|corinthians|galatians|ephesians|philippians|colossians|thessalonians|"
          "timothy|titus|philemon|hebrews|james|peter|jude|revelation")
SCRIPTURE = re.compile(rf"\b(bible|scriptures?|it is written|the word of god|verse \d+)\b|\b(?:[123]\s)?(?:{_BOOKS})\s+\d+(?::\d+)?",
                       re.I)
_PRAYER = re.compile(r"\b(let us pray|let's pray|we pray|i pray|amen|heavenly father|dear (?:lord|god|jesus)|in jesus'? name)\b", re.I)
_STORY = re.compile(r"\b(once|years ago|one day|there was a|a man|a woman|she said|he said|i remember|story)\b", re.I)
_ENCOURAGE = re.compile(r"\b(don'?t give up|keep going|you are (?:loved|not alone|enough|chosen)|god (?:loves|is with|will)|be encouraged|"
                        r"do not fear|never alone|hope)\b", re.I)
_EMOTION = {"love", "hope", "fear", "pain", "cry", "cried", "tears", "joy", "grace", "forgive", "forgiven", "heal", "healed",
            "broken", "lost", "alone", "peace", "heart", "free", "freedom", "grief", "mercy", "weak", "strong", "hurt",
            "suffer", "comfort", "miracle", "trust", "believe", "faith"}
_NEEDS_CONTEXT = re.compile(r"^\s*(and|but|so|then|this|that|these|those|it|he|she|they|him|her|them|as i said|like i said)\b", re.I)


def stem(word: str) -> str:
    return word.strip("'-")[:5]


def _words(text: str) -> list:
    return re.sub(r"[^\w'\- ]", " ", (text or "").lower()).split()


def has_scripture(text: str) -> bool:
    return bool(SCRIPTURE.search(text or ""))


def infer_category(text: str) -> str:
    if has_scripture(text):
        return "Scripture"
    if _PRAYER.search(text or ""):
        return "Prayer"
    if _STORY.search(text or ""):
        return "Story"
    if _ENCOURAGE.search(text or ""):
        return "Encouragement"
    return "Teaching"


def clean_category(category, text: str) -> str:
    """One of the five categories. A claim the clip's own words cannot support (a 'Scripture' clip that never
    mentions the Bible, a 'Prayer' with no prayer) is replaced by what the text shows."""
    raw = str(category or "").strip().lower()
    cat = next((c for c in CATEGORIES if c.lower() == raw or (raw and (raw.startswith(c.lower()[:5]) or c.lower().startswith(raw[:5])))), None)
    if cat is None:
        return infer_category(text)
    if cat == "Scripture" and not has_scripture(text):
        return infer_category(text) if infer_category(text) != "Scripture" else "Teaching"
    if cat == "Prayer" and not _PRAYER.search(text or "") and not re.search(r"\b(pray|prayer|lord|father)\b", text or "", re.I):
        return "Teaching"
    return cat


# ------------------------------------------------------------------ the Message score
def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def parse_score(item: dict):
    """(score 0-100 or None, parts dict or None) from what the model returned.

    Preferred: ``score`` is an object with the five parts (each 0-20, summed). A plain number is also accepted
    (0-100, or 0-10 / 0-1 scales are scaled up). Anything else: None, so the caller can estimate."""
    raw = item.get("score", item.get("message_score"))
    if isinstance(raw, dict):
        parts = {}
        for k in CRITERIA:
            v = _num(raw.get(k))
            if v is not None:
                parts[k] = int(round(max(0.0, min(float(PART_MAX), v))))
        if len(parts) >= 3:                                   # one or two missing parts count as the average of the rest
            avg = sum(parts.values()) / len(parts)
            for k in CRITERIA:
                parts.setdefault(k, int(round(avg)))
            return min(100, sum(parts.values())), parts
        return None, None
    v = _num(raw)
    if v is None or isinstance(raw, bool):
        return None, None
    if v <= 1.0:
        v *= 100
    elif v <= 10.0:
        v *= 10
    return int(round(max(0.0, min(100.0, v)))), None


def estimate_score(text: str, seconds: float = 0.0):
    """A plain-text estimate of the same five parts, used only when the model gave no score."""
    sents = [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]
    first = sents[0] if sents else ""
    fw = len(first.split())
    opening = 20 if fw and fw <= 12 and first.rstrip().endswith(("?", "!")) else 12 if fw and fw <= 14 else 6
    short_sents = sum(1 for s in sents if 3 <= len(s.split()) <= 10)
    quotable = 20 if has_scripture(text) else min(14, 6 + 2 * short_sents)
    emo = min(20, 4 * sum(1 for w in _words(text) if w in _EMOTION))
    standalone = 4 if _NEEDS_CONTEXT.match(first) else 14
    ends_whole = 4 if sents and sents[-1].rstrip().endswith((".", "?", "!")) else 0
    idea = 10 + ends_whole + (4 if 3 <= len(sents) <= 12 else 0)
    parts = {"opening": opening, "idea": min(PART_MAX, idea), "quotable": quotable, "emotion": emo, "standalone": standalone}
    return min(100, sum(parts.values())), parts


# ------------------------------------------------------------------ "Find a moment about ..."
def clean_topic(topic) -> str:
    return re.sub(r"\s+", " ", str(topic or "")).strip()[:200]


def topic_stems(topic: str) -> list:
    out = []
    for w in keywords.content_words(topic, min_len=3):
        s = stem(w)
        if s and s not in out:
            out.append(s)
    return out[:8]


def topic_hits(topic: str, text: str) -> float:
    """Share (0-1) of the topic's words that really occur in the clip's own text (compared on their first 5 letters)."""
    stems = topic_stems(topic)
    if not stems:
        return 0.0
    have = {stem(w) for w in _words(text)}
    return sum(1 for s in stems if s in have) / len(stems)


def rank_value(clip: dict, topic: str = "") -> float:
    """What clips are ordered by: the message score, plus - only when a topic was asked for - a bonus that needs the
    topic's words to appear in the clip (the model's own 'topic_match' claim alone counts for very little)."""
    base = float(clip.get("score") if clip.get("score") is not None else 0)
    if not topic:
        return base
    hits = float(clip.get("topic_hits", 0.0))
    llm = max(0.0, min(100.0, _num(clip.get("topic_llm")) or 0.0))
    return base + 40.0 * hits + (0.3 * llm if hits > 0 else 0.0)        # a claim with no matching words adds nothing


# ------------------------------------------------------------------ the pick
def overlap(a, b) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def pick(cands: list, count: int, used: list, topic: str = "", slack: float = 0.5) -> tuple:
    """Best-ranked candidates that do not overlap (more than ``slack`` seconds) the ``used`` stretches or each other.

    Returns (kept, rest) - ``rest`` is every candidate not kept, still best first, for later repair."""
    ranked = sorted(cands, key=lambda c: (-rank_value(c, topic), c["start"]))
    kept, rest, taken = [], [], list(used)
    for c in ranked:
        if len(kept) < count and all(overlap((c["start"], c["end"]), t) <= slack for t in taken):
            kept.append(c)
            taken.append((c["start"], c["end"]))
        else:
            rest.append(c)
    return kept, rest
