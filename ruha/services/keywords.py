"""One shared "which words of a hook get the accent colour" rule (used by the reel cards, graphics and
Creative Studio typography, which each had their own copy)."""
from __future__ import annotations

import re

STOP = {"the", "and", "for", "that", "this", "with", "you", "your", "are", "not", "but", "his", "her", "its",
        "from", "have", "has", "was", "were", "will", "into", "over", "they", "them", "our", "all", "can",
        "who", "what", "when", "then", "than", "join", "stop", "keep", "never", "every", "against"}

_STRIP = ".,!?;:\"'()"


def clean(word: str) -> str:
    return (word or "").strip(_STRIP).lower()


def key_words(text: str, n: int = 2) -> set:
    """The strongest ``n`` words of a hook (the longest non-trivial words, 5+ letters) - they get the accent colour."""
    words = [clean(w) for w in (text or "").split()]
    cand = sorted({w for w in words if len(w) > 4 and w not in STOP}, key=len, reverse=True)
    return set(cand[:n])


def highlight_html(text: str, esc=None, cls: str = "k") -> str:
    """``text`` with its key words wrapped in <span class="k">. ``esc`` escapes one word (default: html.escape)."""
    import html
    esc = esc or html.escape
    keys = key_words(text)
    return " ".join(f'<span class="{cls}">{esc(w)}</span>' if clean(w) in keys else esc(w) for w in (text or "").split())


def content_words(text: str, min_len: int = 4) -> list:
    """Lower-case words of ``min_len``+ letters that are not filler - used to compare a hook with its source text."""
    return [w for w in (re.sub(r"[^\w'\- ]", " ", (text or "").lower()).split())
            if len(w.strip("'-")) >= min_len and w.strip("'-") not in STOP]
