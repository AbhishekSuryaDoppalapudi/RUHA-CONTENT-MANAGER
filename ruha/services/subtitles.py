"""SRT generation for reels and full videos."""
from __future__ import annotations

import textwrap
from pathlib import Path


def srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        sec, ms = sec + 1, 0
    h, m, s = int(sec // 3600), int(sec % 3600 // 60), int(sec % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _chunks(text: str, max_chars: int) -> list:
    """Split a long caption into <= 2 lines of max_chars, and several cues if needed."""
    text = " ".join(text.split())
    if not text:
        return []
    per_cue = max_chars * 2
    words = text.split(" ")
    cues, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > per_cue:
            cues.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        cues.append(cur)
    return cues


def build_cues(segments, start: float, end: float, max_chars: int = 28) -> list:
    """Return [(start, end, text)] relative to ``start`` for the window [start, end]."""
    cues = []
    for seg in segments:
        s_start, s_end = float(seg["start"]), float(seg["end"])
        if s_end <= start or s_start >= end:
            continue
        a, b = max(s_start, start), min(s_end, end)
        pieces = _chunks(seg["text"], max_chars)
        if not pieces or b <= a:
            continue
        total = sum(len(p) for p in pieces) or 1
        t = a
        for p in pieces:
            d = (b - a) * len(p) / total
            cues.append((t - start, t + d - start, "\n".join(textwrap.wrap(p, max_chars)) or p))
            t += d
    return cues


def build_srt(segments, start: float, end: float, path, max_chars: int = 28) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (a, b, text) in enumerate(build_cues(segments, start, end, max_chars), 1):
        lines.append(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def full_srt(segments, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for i, s in enumerate(segments, 1):
        out.append(f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text'].strip()}\n")
    path.write_text("\n".join(out), encoding="utf-8")
    return path
