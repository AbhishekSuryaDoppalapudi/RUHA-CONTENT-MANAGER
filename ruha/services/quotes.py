"""The strongest sentence of a clip, word for word from the transcript, and the poster text that goes with it.

A poster quotes the speaker, so the quote is never written by the model: it is a sentence of the clip's own transcript,
chosen by how quotable it is (names of God, Bible references, strong words, a question or a bold statement, one complete
thought that makes sense alone) and by whether the model's own list of verbatim quotes agrees.
"""
from __future__ import annotations

import re

from ruha.services import emphasis, hooks

MIN_WORDS, MAX_WORDS, IDEAL = 5, 30, (8, 22)
_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+")
_LEAD = re.compile(r"^(and|but|so|then|because|well|now|okay|ok|yeah|right|also|or|which|that|for)\b[,\s]+", re.I)
_NEEDS_CONTEXT = re.compile(r"^(this|that|these|those|it|he|she|they|him|her|them|there|here)\b", re.I)
CATEGORY_LABEL = {"Scripture": "Scripture", "Encouragement": "Encouragement", "Story": "Story", "Teaching": "Teaching", "Prayer": "Prayer"}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower(), flags=re.UNICODE).split())


def sentences(segments, start: float, end: float) -> list:
    """[(text, start, end)] - the sentences spoken in [start, end]. Whisper cuts its segments anywhere, so a sentence can run
    across several of them: the text is joined first and split at . ? ! afterwards. Times are interpolated by character."""
    full, spans = "", []                                     # spans: (char start, char end, seg start, seg end)
    for seg in segments:
        s0, s1 = float(seg["start"]), float(seg["end"])
        if s1 <= start or s0 >= end:
            continue
        txt = " ".join(str(seg["text"]).split())
        if not txt:
            continue
        spans.append((len(full), len(full) + len(txt), s0, s1))
        full += txt + " "

    def at(pos: int) -> float:
        for c0, c1, t0, t1 in spans:
            if c0 <= pos <= c1:
                return t0 + (t1 - t0) * (pos - c0) / max(1, c1 - c0)
        return spans[-1][3] if spans else start
    out, pos = [], 0
    for m in _SPLIT.finditer(full):
        text = full[pos:m.start() + 1].strip() if full[m.start():m.start() + 1] in ".!?" else full[pos:m.start()].strip()
        if text:
            out.append((text, at(pos), at(m.start())))
        pos = m.end()
    tail = full[pos:].strip()
    if tail:
        out.append((tail, at(pos), spans[-1][3] if spans else end))
    return out


def sentence_score(text: str, llm_quotes=()) -> float:
    """How quotable one sentence is (higher = better); 0 or less means do not use it."""
    words = text.split()
    n = len(words)
    if n < MIN_WORDS or n > MAX_WORDS:
        return -1.0
    sc = emphasis.word_scores(words)
    s = 0.0
    if any(x >= 100 for x in sc):
        s += 3.0                                                 # names God
    if any(x >= 90 for x in sc):
        s += 3.0                                                 # a Bible reference
    s += min(3, sum(1 for x in sc if x == 60)) * 1.0             # strong vocabulary
    s += min(2, sum(1 for x in sc if x >= 25 and x < 60)) * 0.3
    s += 0.6 if text.rstrip().endswith("?") else 0.0
    s += 0.4 if text.rstrip().endswith("!") else 0.0
    s += 0.5 if text.rstrip().endswith((".", "?", "!", "\"", "'")) else -0.5
    lo, hi = IDEAL
    s += 1.0 if lo <= n <= hi else 0.0
    s -= 1.5 if _NEEDS_CONTEXT.match(text) else 0.0              # "This is why ..." means nothing alone
    s -= 0.6 if _LEAD.match(text) else 0.0
    s -= 0.5 * len(re.findall(r"\b(you know|i mean|kind of|sort of|um|uh)\b", text, re.I))
    s += 0.3 if text[:1].isupper() else -0.3
    nt = _norm(text)
    if any(q and (_norm(q) in nt or nt in _norm(q)) for q in llm_quotes if len(str(q).split()) >= 4):
        s += 4.0                                                 # the model also picked it as a verbatim quote
    return s


def _clean(text: str) -> str:
    """Drop a leading "And"/"But"/"So" - the rest is still word for word the speaker's - and start with a capital."""
    t = text.strip()
    m = _LEAD.match(t)
    if m and len(t.split()) - len(m.group(0).split()) >= MIN_WORDS:
        t = t[m.end():].lstrip()
    return (t[:1].upper() + t[1:]) if t else t


def _candidates(text: str) -> list:
    """The sentence itself and - when it is too long to quote - every run of its comma-separated clauses that is short enough.
    Every candidate is a stretch of the sentence word for word."""
    if len(text.split()) <= MAX_WORDS:
        return [text]
    pieces = re.split(r"(?<=,)\s+", text)
    out = []
    for i in range(len(pieces)):
        for j in range(i, len(pieces)):
            cand = " ".join(pieces[i:j + 1]).strip(" ,")
            if MIN_WORDS <= len(cand.split()) <= MAX_WORDS:
                out.append(cand)
    return out


def strongest_sentence(segments, start: float, end: float, llm_quotes=()) -> dict | None:
    """The most quotable sentence spoken in [start, end]: {"text", "start", "end", "score", "reference"} or None.
    A sentence too long for a poster is quoted from its strongest clauses."""
    best = None
    units = sentences(segments, start, end)
    for seg in segments:                                          # a transcript without punctuation: each segment is a unit too
        s0, s1 = float(seg["start"]), float(seg["end"])
        if s1 > start and s0 < end:
            units.append((" ".join(str(seg["text"]).split()), s0, s1))
    for text, a, b in units:
        for cand in _candidates(text):
            sc = sentence_score(cand, llm_quotes)
            if sc > 0 and (best is None or sc > best[0]):
                best = (sc, cand, a, b)
    if best is None:                                              # nothing scores: the longest complete sentence
        cand = [(len(t.split()), t, a, b) for t, a, b in sentences(segments, start, end) if MIN_WORDS <= len(t.split()) <= MAX_WORDS]
        if not cand:
            return None
        n, text, a, b = max(cand)
        best = (0.0, text, a, b)
    sc, text, a, b = best
    text = _clean(text)
    return {"text": text, "start": round(a, 2), "end": round(b, 2), "score": round(sc, 2), "reference": reference_in(text)}


def reference_in(text: str) -> str:
    """The Bible reference spoken inside ``text`` ("John 3:16"), or ""."""
    words = text.split()
    spans = emphasis.reference_spans(words)
    if not spans:
        return ""
    a, b = spans[0]
    return " ".join(w.strip(emphasis.TRIM) for w in words[a:b])


def is_verbatim(quote: str, full_text: str) -> bool:
    """True when ``quote`` is in the transcript word for word (case and punctuation aside)."""
    q = _norm(quote)
    return bool(q) and q in _norm(full_text)


def clips_of(plan: dict) -> list:
    return list(plan.get("main_reels") or []) + list(plan.get("short_reels") or [])


def best_clip(plan: dict) -> dict | None:
    """The best clip of a plan: highest Message score (a typed clip has none); the main reel wins a tie."""
    clips = [c for c in clips_of(plan) if isinstance(c, dict) and "start" in c]
    if not clips:
        return None
    return max(clips, key=lambda c: (c.get("score") if isinstance(c.get("score"), (int, float)) else -1,
                                     1 if c in (plan.get("main_reels") or []) else 0))


def poster_label(clip: dict | None, plan: dict) -> str:
    """The small label on a poster, from the plan: the clip's category (Scripture, Teaching ...), else the clip's hook, else the
    poster's own hook - never a fixed word."""
    if clip and clip.get("category") in CATEGORY_LABEL:
        return CATEGORY_LABEL[clip["category"]]
    for src in ((clip or {}).get("hook"), (plan.get("poster") or {}).get("headline"), plan.get("title")):
        h = hooks.tidy_hook(str(src or ""), 3)
        if h:
            return h
    return plan.get("main_theme", "")[:24]


def poster_from_clip(plan: dict, segments, full_text: str = "") -> dict:
    """The poster fields taken from the plan's best clip: its strongest sentence (verbatim) as the quote, the Bible
    reference spoken in that sentence, and the label. {} when the plan has no clip or the clip has no usable sentence."""
    clip = best_clip(plan)
    if not clip:
        return {}
    hit = strongest_sentence(segments, clip["start"], clip["end"], plan.get("quotes") or [])
    if not hit or (full_text and not is_verbatim(hit["text"], full_text)):
        return {"label": poster_label(clip, plan), "clip": {"start": clip["start"], "end": clip["end"]}}
    return {"quote": hit["text"], "subheadline": hit["reference"], "label": poster_label(clip, plan),
            "clip": {"start": clip["start"], "end": clip["end"], "quote_start": hit["start"], "quote_end": hit["end"]}}
