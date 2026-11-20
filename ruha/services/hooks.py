"""Reel hooks: a 3-8 word phrase taken from the sermon's own words.

The model is asked for such a phrase, but small models drift (a summary, a slogan, one word), so the rule is
enforced here and never depends on the model: a hook must be 3-8 whole words, and its content words must come
from the clip's own transcript. Anything else is replaced by a phrase extracted from the clip itself.
A single bold word is still available as an opt-in setting (``hook.one_word``, off by default).
"""
from __future__ import annotations

import re

from ruha.services import keywords

MIN_WORDS, MAX_WORDS, MAX_CHARS = 3, 8, 60
GROUNDED_RATIO = 0.8         # at least 80% of a hook's content words must occur in the clip's own text

TRAIL = {"and", "or", "in", "of", "to", "the", "a", "an", "our", "your", "my", "for", "with", "on", "at", "by",
         "is", "are", "but", "so", "that", "because"}
_LEAD = {"so", "and", "but", "now", "well", "okay", "ok", "oh", "yeah", "then", "also", "just", "like", "see"}
_SENT = re.compile(r"(?<=[.!?])\s+")


def tidy_hook(text: str, max_words: int = MAX_WORDS, max_chars: int = MAX_CHARS) -> str:
    """Whole words only: never cut a word in half, and don't end on a dangling 'and', 'in', 'our'..."""
    words = str(text or "").strip().strip('"“”').split()[:max_words]
    while words and len(" ".join(words)) > max_chars:
        words.pop()
    while len(words) > 2 and words[-1].lower().strip(".,;:!?") in TRAIL:
        words.pop()
    return " ".join(words)


def _stem(w: str) -> str:
    return w.strip("'-")[:5]


def grounded(hook: str, source: str, min_ratio: float = GROUNDED_RATIO) -> bool:
    """True when the hook's content words come from ``source`` (compared on their first five letters)."""
    words = keywords.content_words(hook)
    if not words:
        return True
    have = {_stem(w) for w in keywords.content_words(source, min_len=3)}
    return sum(1 for w in words if _stem(w) in have) / len(words) >= min_ratio


def sentences(text: str) -> list:
    return [s.strip() for s in _SENT.split(re.sub(r"\s+", " ", text or "").strip()) if s.strip()]


def _score(sentence: str, n_words: int) -> float:
    strong = len(keywords.content_words(sentence, min_len=5))
    return (3.0 if sentence.rstrip().endswith("?") else 0.0) + (1.0 if sentence.rstrip().endswith("!") else 0.0) \
        + strong - 0.3 * abs(n_words - 6)


def extract_hook(text: str) -> str:
    """A 3-8 word phrase taken verbatim from the clip's own text: a short sentence (questions first), else the
    first clause of a longer one."""
    best, best_score = "", -1e9
    for s in sentences(text):
        words = [w for w in s.split() if w]
        while words and words[0].lower().strip(".,;:!?") in _LEAD and len(words) > MIN_WORDS:
            words.pop(0)
        if len(words) < MIN_WORDS:
            continue
        cand = " ".join(words)
        if len(words) > MAX_WORDS:                       # take the first clause, else the first eight words
            clause = re.split(r"[,;:—-]\s", cand)[0].split()
            cand = " ".join(clause if MIN_WORDS <= len(clause) <= MAX_WORDS else words[:MAX_WORDS])
        cand = tidy_hook(cand)
        n = len(cand.split())
        meaningful = [w for w in keywords.content_words(cand, min_len=4) if w.strip("'-") not in _LEAD | {"then", "yeah"}]
        if n < MIN_WORDS or not meaningful:                                           # all filler is not a hook
            continue
        sc = _score(s, n)
        if sc > best_score:
            best, best_score = cand, sc
    return best.rstrip(",;:-").strip()


def ensure_hook(hook: str, clip_text: str = "", one_word: bool = False, theme: str = "", context: str = "") -> str:
    """The hook to show on a reel's opening card.

    Default: the model's hook if it is 3-8 whole words taken from the clip; otherwise a phrase extracted from the
    clip. With ``one_word`` (opt-in setting) the old single bold word is returned instead.
    """
    if one_word:
        from ruha.services import planner
        return planner.one_word_hook(hook, theme, f"{context} {clip_text}")
    h = tidy_hook(hook)
    n = len(h.split())
    if MIN_WORDS <= n <= MAX_WORDS and (not clip_text or grounded(h, clip_text)):
        return h
    return extract_hook(clip_text) or h or tidy_hook(clip_text, 6)


def clip_text(segments: list, start: float, end: float) -> str:
    return " ".join(s["text"] for s in segments if s["end"] > start and s["start"] < end).strip()
