"""Carousel slides for the automatic pipeline: a cover, 3-5 slides of the sermon's main points, and a closing slide (5-7 in all).

The points come from the plan (the model's ``carousel`` list, else its ``key_points``, else verbatim quotes). A point is kept only
when it is short, is a point and not filler, and rests on what was really said: most of its content words must occur in the
transcript. Near-duplicates are dropped. ``plan["carousel_points"]`` holds the clean candidates; ``assemble`` builds the slides for
the number the owner asked for, so changing the slide count needs no new plan.
"""
from __future__ import annotations

import re

from ruha.services import emphasis, hooks

MIN_SLIDES, MAX_SLIDES, DEFAULT_SLIDES = 5, 7, 6
MIN_WORDS, MAX_WORDS = 4, 30
TITLE_MAX = 6
GROUNDED = 0.5
MAX_POINTS = MAX_SLIDES - 2


def _words(text: str) -> list:
    return [w for w in re.split(r"\s+", str(text or "").strip()) if w]


def title_from(text: str) -> str:
    """A 2-6 word title for a point, taken from the point's own words (the stretch that carries the most meaning)."""
    from ruha.services.thumbnail import _best_window
    w = _words(text)
    if len(w) <= TITLE_MAX:
        return " ".join(w).strip(" .,;:")
    return " ".join(_best_window(w, 2, TITLE_MAX)).strip(" .,;:")


def clean_point(title, text, full_text: str) -> dict | None:
    """{"title", "text"} or None when this is not a usable point."""
    text = " ".join(str(text or "").split()).strip()
    n = len(text.split())
    if n < MIN_WORDS or n > MAX_WORDS:
        return None
    if all(emphasis.is_filler(w) for w in text.split()):
        return None
    if full_text and not hooks.grounded(text, full_text, GROUNDED):            # a point the speaker never made
        return None
    t = " ".join(str(title or "").split()).strip(" .,;:")
    if not t or len(t.split()) > 7 or len(t.split()) < 1:
        t = title_from(text)
    return {"title": t, "text": text if text.endswith((".", "!", "?")) else text + "."}


def _overlap(a: str, b: str) -> float:
    sa, sb = {emphasis.stem(w) for w in a.split() if not emphasis.is_filler(w)}, {emphasis.stem(w) for w in b.split() if not emphasis.is_filler(w)}
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def candidates(raw, plan: dict, full_text: str) -> list:
    """The clean, distinct main points of a plan, best source first: the model's carousel list, its key points, then verbatim quotes."""
    out = []

    def add(title, text):
        p = clean_point(title, text, full_text)
        if p and all(_overlap(p["text"], q["text"]) < 0.7 for q in out):
            out.append(p)
    for it in (raw if isinstance(raw, list) else []):
        if isinstance(it, dict):
            add(it.get("title") or it.get("headline"), it.get("text") or it.get("body") or it.get("point"))
        elif isinstance(it, str):
            add(None, it)
    for kp in plan.get("key_points") or []:
        add(None, kp)
    for q in plan.get("quotes") or []:
        add(None, q)
    return out[:MAX_POINTS]


def target_slides(value) -> int:
    try:
        return max(MIN_SLIDES, min(MAX_SLIDES, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_SLIDES


def assemble(plan: dict, target=DEFAULT_SLIDES) -> list:
    """The slides: [{"kind": cover|point|closing, "headline", "body", "label", "reference"}]. Five to seven when the sermon gave
    enough points; fewer (never under three) when it did not - nothing is invented to fill the count."""
    target = target_slides(target)
    points = list(plan.get("carousel_points") or [])[:target - 2]
    if not points:
        return []
    poster = plan.get("poster") or {}
    cover_head = hooks.tidy_hook(str(plan.get("title") or poster.get("headline") or ""), 8) or hooks.tidy_hook(plan.get("main_theme", ""), 8)
    theme = " ".join(_words(plan.get("main_theme"))[:20])
    slides = [{"kind": "cover", "headline": cover_head, "body": theme, "label": "Swipe", "reference": ""}]
    for p in points:
        slides.append({"kind": "point", "headline": p["title"], "body": p["text"], "label": "", "reference": ""})
    best = None
    for c in (plan.get("main_reels") or []) + (plan.get("short_reels") or []):
        if isinstance(c, dict) and c.get("closing"):
            best = c
            break
    summary = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(plan.get("summary") or "")) if s.strip()]
    slides.append({"kind": "closing", "headline": (best or {}).get("closing") or "Follow for more",
                   "body": summary[-1] if summary else "Share this with someone who needs to hear it.", "label": "",
                   "reference": poster.get("subheadline", "")})
    n = len(slides)
    for i, s in enumerate(slides, 1):
        s["label"] = s["label"] or f"{i} / {n}"
        s["index"], s["total"] = i, n
    return slides
