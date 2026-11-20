"""Manual reel timings and poster instructions, typed by the user and stored with the project.

Reels box, one reel per line (time range, optional main/short label, optional comment):

    12:30-13:45 main - focus on the part about faith, hook should be a question
    1:02:10-1:03:00 short: the story of the lost coin
    750-825

Times may be mm:ss, h:mm:ss or plain seconds. Times are used exactly as typed (never snapped or moved);
the AI only writes the hook, closing line and caption for each reel from that part of the transcript and
the comment. Poster box: free-text instructions for the poster (no times - those are ignored).

Stored in <workspace>/instructions.json, so no database migration is needed.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ruha.services import planner, selection

FILE = "instructions.json"
MIN_REEL, MAX_REEL = 5.0, 180.0          # hard limits (seconds); outside the planner's usual range only warns
MAX_LINES, MAX_REEL_CHARS, MAX_POSTER_CHARS = 13, 4000, 2000      # 1 main + up to 12 short reels

_TIME = r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?"
_LINE = re.compile(rf"^\s*(?P<a>{_TIME})\s*(?:-|–|—|to)\s*(?P<b>{_TIME})\s*(?P<rest>.*)$", re.I)
_REST = re.compile(r"^(?:(?P<label>main|short)\b)?\s*[-–—:]?\s*(?P<note>.*)$", re.I)


class InstructionError(ValueError):
    """Raised with every problem found, joined by '; ', so the user can fix them all at once."""


def parse_time(tok: str) -> float:
    parts = tok.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"'{tok}' is not a time")
    if len(parts) > 1 and any(n >= 60 for n in nums[1:]):
        raise ValueError(f"'{tok}' has minutes or seconds of 60 or more")
    secs = 0.0
    for n in nums:
        secs = secs * 60 + n
    return secs


def fmt_time(t: float) -> str:
    t = int(round(t))
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_reels(text: str, duration: float | None = None, short_range: tuple | None = None):
    """Return (entries, warnings). Raises InstructionError listing every bad line."""
    entries, errors, warnings = [], [], []
    lines = [(n, ln) for n, ln in enumerate((text or "").splitlines(), 1) if ln.strip()]
    if len(lines) > MAX_LINES:
        raise InstructionError(f"At most {MAX_LINES} reels at a time (found {len(lines)}).")
    if len(text or "") > MAX_REEL_CHARS:
        raise InstructionError(f"The reels box is too long (max {MAX_REEL_CHARS} characters).")
    for n, ln in lines:
        m = _LINE.match(ln)
        if not m:
            errors.append(f"Line {n}: start with a time range like 12:30-13:45 (got '{ln.strip()[:40]}')")
            continue
        try:
            a, b = parse_time(m["a"]), parse_time(m["b"])
        except ValueError as e:
            errors.append(f"Line {n}: {e}")
            continue
        if b <= a:
            errors.append(f"Line {n}: the end time must be after the start time")
            continue
        if duration and a >= duration:
            errors.append(f"Line {n}: starts at {fmt_time(a)} but the video is only {fmt_time(duration)} long")
            continue
        if duration and b > duration + 1:
            errors.append(f"Line {n}: ends at {fmt_time(b)} but the video is only {fmt_time(duration)} long")
            continue
        if duration:
            b = min(b, duration)
        length = b - a
        if length < MIN_REEL or length > MAX_REEL:
            errors.append(f"Line {n}: a reel must be {MIN_REEL:.0f}-{MAX_REEL:.0f} seconds (this one is {length:.0f}s)")
            continue
        r = _REST.match(m["rest"].strip())
        label = (r["label"] or "").lower()
        note = (r["note"] or "").strip()[:400]
        if not label:
            label = "main" if length > (short_range or planner.SHORT_RANGE)[1] else "short"
        lo, hi = planner.MAIN_RANGE if label == "main" else (short_range or planner.SHORT_RANGE)
        if not lo <= length <= hi:
            warnings.append(f"Line {n}: {length:.0f}s is outside the usual {lo:.0f}-{hi:.0f}s for a {label} reel; used as typed")
        entries.append({"start": round(a, 2), "end": round(b, 2), "category": label, "note": note, "line": n})
    if errors:
        raise InstructionError("; ".join(errors))
    for i, x in enumerate(entries):
        for y in entries[i + 1:]:
            if min(x["end"], y["end"]) - max(x["start"], y["start"]) > 0:
                warnings.append(f"Lines {x['line']} and {y['line']} overlap in time")
    return entries, warnings


# ------------------------------------------------------------------ storage
def _template(value, strict: bool = False) -> str:
    """The project's caption template ("" = use the Settings default). Strict: an unknown name is an error, not ignored."""
    from ruha.services.subtitles import TEMPLATES
    v = str(value or "").strip().lower()
    if v and v not in TEMPLATES:
        if strict:
            raise InstructionError(f"The caption style must be one of: {', '.join(TEMPLATES)}")
        return ""
    return v


def load(ws: Path) -> dict:
    try:
        d = json.loads((Path(ws) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"reels": str(d.get("reels") or ""), "posters": str(d.get("posters") or ""),
            "topic": selection.clean_topic(d.get("topic")),
            "caption_template": _template(d.get("caption_template")),
            "generated": d.get("generated") if isinstance(d.get("generated"), dict) else {}}


def save(ws: Path, reels: str, posters: str, generated: dict | None = None, topic: str | None = None,
         caption_template: str | None = None) -> None:
    posters = (posters or "").strip()
    if len(posters) > MAX_POSTER_CHARS:
        raise InstructionError(f"The posters box is too long (max {MAX_POSTER_CHARS} characters).")
    cur = load(ws)
    Path(ws).mkdir(parents=True, exist_ok=True)
    (Path(ws) / FILE).write_text(json.dumps(
        {"reels": (reels or "").strip(), "posters": posters,
         "topic": cur["topic"] if topic is None else selection.clean_topic(topic),
         "caption_template": cur["caption_template"] if caption_template is None else _template(caption_template, strict=True),
         "generated": cur["generated"] if generated is None else generated}, ensure_ascii=False, indent=1), encoding="utf-8")


def key_for(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ plan overlay
def _overlap(a, b) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def apply_manual_reels(plan: dict, entries: list, describe, short_count: int | None = None) -> dict:
    """Copy of `plan` where the typed reels come first and the AI's reels fill any remaining slots.

    `describe(entry) -> {hook, closing, caption, hashtags, reason}` writes the text for one typed reel.
    """
    out = dict(plan)
    taken = [(e["start"], e["end"]) for e in entries]          # the AI never reuses a typed stretch
    if short_count is None:
        short_count = int((plan.get("reel_settings") or {}).get("count") or selection.DEFAULT_COUNT)
    for cat, key, count in (("main", "main_reels", planner.N_MAIN), ("short", "short_reels", short_count)):
        clips = []
        for e in sorted((e for e in entries if e["category"] == cat), key=lambda e: e["start"]):
            d = describe(e)
            clips.append({"start": e["start"], "end": e["end"], "manual": True, "note": e["note"],
                          "hook": d["hook"], "closing": d["closing"], "caption": d["caption"],
                          "hashtags": d["hashtags"] or list(plan.get("hashtags", []))[:6], "reason": d["reason"],
                          "key_words": d.get("key_words") or [],
                          "score": None, "score_source": "typed", "category": None})
        for ai in plan.get(key, []):
            if len(clips) >= count:
                break
            if all(_overlap((ai["start"], ai["end"]), t) <= 0.4 * (ai["end"] - ai["start"]) for t in taken):
                clips.append(ai)
                taken.append((ai["start"], ai["end"]))
        out[key] = clips
    return out
