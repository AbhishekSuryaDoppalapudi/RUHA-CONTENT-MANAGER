"""The Reel Editor's data: a reel is an ordered list of segments of the sermon plus how it looks.

Nothing here touches the video. Every change goes through ``update`` (which validates, saves, and sends an approved reel back to
Draft because it is no longer what was approved); rendering is in ``reel_render``."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from ruha.models import EditorReel, ReelSegment, ReelVersion, utcnow
from ruha.services import subtitles, transcripts

ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
STATUSES = ("draft", "ready", "approved", "rendered")
WARN_SECONDS = 60.0
MIN_SEGMENT = 0.3
MAX_SEGMENTS = 40
MAX_TEXT = 600
PLATFORMS = ("instagram", "facebook", "youtube", "x")
FADE = 0.05                                   # seconds of audio fade at every cut, so a join never clicks

DEFAULT_OPTIONS = {
    "caption": {"enabled": True, "template": "wordpop", "font": "", "color": "#FFFFFF", "size": 78},
    "hook": {"enabled": False, "text": ""},
    "lower_third": {"enabled": False, "name": "", "role": ""},
    "watermark": {"enabled": True},
    "end_card": {"enabled": False, "text": ""},
    "music": {"path": "", "volume": 0.15},
    "thumbnail": {"time": None, "text": "", "path": ""},
    "posts": {p: "" for p in PLATFORMS},
}


class EditorError(ValueError):
    """A problem the owner can fix; the message is shown as it is."""


def merged_options(stored: dict | None, patch: dict | None = None) -> dict:
    """The defaults, overlaid with what is stored, overlaid with ``patch`` - one level of keys deep, validated."""
    out = copy.deepcopy(DEFAULT_OPTIONS)
    for src in (stored or {}, patch or {}):
        for k, v in src.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k].update({kk: vv for kk, vv in v.items() if kk in out[k]})
    c = out["caption"]
    if c["template"] not in subtitles.TEMPLATES:
        raise EditorError(f"The caption style must be one of: {', '.join(subtitles.TEMPLATES)}")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(c["color"])):
        raise EditorError("The caption colour must look like #FFFFFF")
    try:
        c["size"] = max(24, min(160, int(c["size"])))
        out["music"]["volume"] = max(0.0, min(1.0, float(out["music"]["volume"])))
    except (TypeError, ValueError):
        raise EditorError("Caption size and music volume must be numbers")
    for sect, key in (("hook", "text"), ("end_card", "text"), ("lower_third", "name"), ("lower_third", "role")):
        out[sect][key] = str(out[sect][key] or "").strip()[:MAX_TEXT]
    out["posts"] = {p: str(out["posts"].get(p) or "")[:5000] for p in PLATFORMS}
    return out


# ------------------------------------------------------------------ segments
def word_list(segs: list) -> list:
    return [w for x in transcripts.sentences_of(segs) for w in x["words"]]


def snap(start: float, end: float, words: list, duration: float) -> tuple:
    """A cut that lands between words, in the pause, never inside a word: the start moves to the middle of the pause before
    the first word it touches, the end to the middle of the pause after the last word (by at most 0.25 s either way)."""
    start, end = max(0.0, float(start)), min(float(duration), float(end))
    if not words:
        return round(start, 3), round(end, 3)
    inside = [w for w in words if (w["start"] + w["end"]) / 2 >= start and (w["start"] + w["end"]) / 2 <= end]
    if not inside:
        return round(start, 3), round(end, 3)
    first, last = inside[0], inside[-1]
    prev = [w for w in words if w["end"] <= first["start"]]
    nxt = [w for w in words if w["start"] >= last["end"]]
    a = first["start"] if not prev else (prev[-1]["end"] + first["start"]) / 2
    b = last["end"] if not nxt else (last["end"] + nxt[0]["start"]) / 2
    a, b = max(a, first["start"] - 0.25), min(b, last["end"] + 0.25)
    return round(max(0.0, a), 3), round(min(float(duration), b), 3)


def clean_segments(raw, duration: float, words: list | None = None, do_snap: bool = True) -> list:
    """[{"start","end"}] validated against the sermon length, snapped to the pauses between words. Raises EditorError."""
    if not isinstance(raw, list):
        raise EditorError("The segments must be a list")
    if len(raw) > MAX_SEGMENTS:
        raise EditorError(f"A reel can join at most {MAX_SEGMENTS} segments")
    out = []
    for i, r in enumerate(raw, 1):
        try:
            a, b = float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError):
            raise EditorError(f"Segment {i} needs a start and an end time in seconds")
        if a < 0 or b <= a:
            raise EditorError(f"Segment {i}: the end must be after the start")
        if duration and a >= duration:
            raise EditorError(f"Segment {i} starts after the end of the sermon")
        if do_snap:
            a, b = snap(a, b, words or [], duration or b)
        if b - a < MIN_SEGMENT:
            raise EditorError(f"Segment {i} is shorter than {MIN_SEGMENT:g} seconds")
        out.append({"start": round(a, 3), "end": round(b, 3)})
    return out


def total_seconds(segs: list) -> float:
    return round(sum(max(0.0, s["end"] - s["start"]) for s in segs), 3)


# ------------------------------------------------------------------ rows <-> dicts
def as_dict(r: EditorReel) -> dict:
    segs = [{"start": g.start, "end": g.end} for g in r.segments]
    total = total_seconds(segs)
    warnings = [f"This reel is {total:.0f} s - longer than {WARN_SECONDS:.0f} s."] if total > WARN_SECONDS else []
    return {"id": r.id, "project_id": r.project_id, "title": r.title, "status": r.status, "version": r.version,
            "language": r.language, "aspect": r.aspect, "source": r.source, "segments": segs, "total_seconds": total,
            "options": merged_options(r.options), "caption_edits": r.caption_edits, "warnings": warnings,
            "has_preview": bool(r.preview_path), "asset_id": r.asset_id,
            "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat()}


def get(s, reel_id: int) -> EditorReel:
    r = s.get(EditorReel, reel_id)
    if r is None:
        raise LookupError("Reel not found")
    return r


def _words_and_duration(s, project_id: int) -> tuple:
    t = transcripts.latest(s, project_id)
    if t is None:
        raise EditorError("This sermon has no transcript yet - run transcription first")
    segs = transcripts.stored_segments(s, t.id)
    return word_list(segs), (segs[-1]["end"] if segs else 0.0)


def create(s, project_id: int, title: str = "", segments=None, options=None, source: str = "manual",
           language: str = "English", aspect: str = "9:16") -> EditorReel:
    words, duration = _words_and_duration(s, project_id)
    if aspect not in ASPECTS:
        raise EditorError(f"The aspect ratio must be one of: {', '.join(ASPECTS)}")
    r = EditorReel(project_id=project_id, title=(title or "Untitled reel").strip()[:300], source=source,
                   language=language, aspect=aspect, status="draft",
                   options_json=json.dumps(merged_options(None, options), ensure_ascii=False),
                   caption_edits_json="{}")
    for i, g in enumerate(clean_segments(segments or [], duration, words)):
        r.segments.append(ReelSegment(position=i, start=g["start"], end=g["end"]))
    s.add(r)
    s.flush()
    return r


def update(s, reel_id: int, patch: dict) -> EditorReel:
    """Apply an edit. Returns the reel; an approved/rendered reel goes back to Draft (it changed after it was approved)."""
    r = get(s, reel_id)
    changed = False
    if "title" in patch:
        r.title = str(patch["title"] or "").strip()[:300] or r.title
        changed = True
    if "language" in patch:
        r.language = str(patch["language"] or "English")[:40]
        changed = True
    if "aspect" in patch:
        if patch["aspect"] not in ASPECTS:
            raise EditorError(f"The aspect ratio must be one of: {', '.join(ASPECTS)}")
        r.aspect = patch["aspect"]
        changed = True
    if "segments" in patch:
        words, duration = _words_and_duration(s, r.project_id)
        new = clean_segments(patch["segments"], duration, words, do_snap=bool(patch.get("snap", True)))
        r.segments[:] = [ReelSegment(position=i, start=g["start"], end=g["end"]) for i, g in enumerate(new)]
        changed = True
    if "options" in patch:
        r.options_json = json.dumps(merged_options(r.options, patch["options"]), ensure_ascii=False)
        changed = True
    if "caption_edits" in patch:
        edits = dict(r.caption_edits)
        for k, v in (patch["caption_edits"] or {}).items():
            if not re.fullmatch(r"\d+:\d+", str(k)):
                raise EditorError(f"'{k}' is not a word of the transcript")
            v = str(v or "").strip()
            if v:
                edits[str(k)] = v[:80]
            else:
                edits.pop(str(k), None)                       # an empty correction means "back to what was spoken"
        r.caption_edits_json = json.dumps(edits, ensure_ascii=False)
        changed = True
    if changed and r.status in ("ready", "approved", "rendered"):
        r.status = "draft"
    r.updated_at = utcnow()
    s.flush()
    return r


def duplicate(s, reel_id: int) -> EditorReel:
    src = get(s, reel_id)
    d = as_dict(src)
    r = EditorReel(project_id=src.project_id, title=f"{src.title} (copy)"[:300], source=f"copy:{src.id}", language=src.language,
                   aspect=src.aspect, status="draft", caption_edits_json=src.caption_edits_json)
    r.options_json = json.dumps({**merged_options(src.options), "thumbnail": DEFAULT_OPTIONS["thumbnail"]}, ensure_ascii=False)
    for i, g in enumerate(d["segments"]):
        r.segments.append(ReelSegment(position=i, start=g["start"], end=g["end"]))
    s.add(r)
    s.flush()
    return r


def save_version(s, reel_id: int, note: str = "") -> dict:
    """A named snapshot of the reel (segments, look, caption corrections) that stays when it is edited again."""
    r = get(s, reel_id)
    snap_ = {k: v for k, v in as_dict(r).items() if k in ("title", "language", "aspect", "segments", "options", "caption_edits")}
    last = s.query(ReelVersion).filter_by(reel_id=r.id).order_by(ReelVersion.version.desc()).first()
    n = (last.version if last else 0) + 1
    s.add(ReelVersion(reel_id=r.id, version=n, snapshot_json=json.dumps(snap_, ensure_ascii=False), note=str(note or "")[:200]))
    r.version = n
    s.flush()
    return {"version": n, "note": note, "snapshot": snap_}


def versions(s, reel_id: int) -> list:
    get(s, reel_id)
    return [{"version": v.version, "note": v.note, "created_at": v.created_at.isoformat(), "snapshot": json.loads(v.snapshot_json)}
            for v in s.query(ReelVersion).filter_by(reel_id=reel_id).order_by(ReelVersion.version.desc())]


def restore_version(s, reel_id: int, version: int) -> EditorReel:
    v = s.query(ReelVersion).filter_by(reel_id=reel_id, version=int(version)).first()
    if v is None:
        raise LookupError("Version not found")
    snap_ = json.loads(v.snapshot_json)
    r = update(s, reel_id, {"title": snap_["title"], "language": snap_["language"], "aspect": snap_["aspect"],
                            "segments": snap_["segments"], "snap": False, "options": snap_["options"]})
    r.caption_edits_json = json.dumps(snap_.get("caption_edits") or {}, ensure_ascii=False)
    return r


# ------------------------------------------------------------------ the text of the reel (captions with the owner's corrections)
def virtual_segments(reel: dict, stored: list) -> list:
    """The reel's words on the reel's own timeline (0 = first frame), with the owner's caption corrections applied: transcript-
    segment-shaped dicts ready for ``subtitles.build_cues`` / ``clip_words``. Corrections change text only, never a time."""
    edits, out, offset = reel.get("caption_edits") or {}, [], 0.0
    for g in reel["segments"]:
        shift = offset - g["start"]
        words = []
        for seg in stored:
            if seg["end"] <= g["start"] or seg["start"] >= g["end"]:
                continue
            for w in transcripts.words_of(seg):
                mid = (w["start"] + w["end"]) / 2
                if g["start"] <= mid <= g["end"]:
                    words.append({"w": edits.get(w["id"], w["w"]), "s": round(w["start"] + shift, 3), "e": round(w["end"] + shift, 3)})
        if words:
            out.append({"start": words[0]["s"], "end": words[-1]["e"], "text": " ".join(x["w"] for x in words), "words": words})
        offset += g["end"] - g["start"]
    return out


def reel_text(reel: dict, stored: list) -> str:
    return " ".join(x["text"] for x in virtual_segments(reel, stored))


def cut_key(video_id, segments: list, aspect: str, mode: str) -> str:
    """What the joined video depends on - and nothing else. Same key = the cached cut is still right."""
    raw = json.dumps([video_id, [[round(g["start"], 3), round(g["end"], 3)] for g in segments], aspect, mode, FADE, "q2"])   # q2 = preview at half size
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def sentence_selection(sentences: list, first: int, last: int) -> dict:
    """The time range of whole sentences first..last (inclusive) - what a click on a sentence adds."""
    chosen = [x for x in sentences if first <= x["id"] <= last]
    if not chosen:
        raise EditorError("No such sentence")
    return {"start": chosen[0]["start"], "end": chosen[-1]["end"]}


def posts_draft(title: str, text: str, hashtags=()) -> dict:
    """A starting text for every platform from the reel's own words (no AI call - instant). The owner edits it."""
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
    lead = sents[0] if sents else title
    tags = " ".join(h if h.startswith("#") else f"#{h}" for h in list(hashtags)[:5])
    short = (lead[:117] + "...") if len(lead) > 120 else lead
    body = " ".join(sents[:3])
    return {"instagram": f"{lead}\n\n{title}\n\n{tags}".strip(),
            "facebook": f"{title}\n\n{body}\n\nWatch the full message for more.".strip(),
            "youtube": f"{title}\n\n{body}\n\n{tags}".strip(),
            "x": f"{short} {tags}".strip()[:270]}
