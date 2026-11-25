"""The sermon transcript as the Reel Editor needs it: sentences made of words, each word with its own time and a stable id.

A word's id is "<transcript segment idx>:<word idx>", so a caption correction stays attached to the right word. Words come from
the stored word timestamps; a segment that has none (an older transcript) gets its words spread evenly over its time and each
such word is marked ``estimated``. The transcript is read from the database - never transcribed again."""
from __future__ import annotations

import json
import re

from ruha.models import SourceVideo, Transcript, TranscriptSegment

_END = re.compile(r"[.!?…][\"')\]]*$")


def words_of(seg: dict) -> list:
    """[{"id", "w", "start", "end", "confidence", "estimated"}] for one stored segment ({"idx","start","end","text","words"})."""
    out = []
    raw = seg.get("words") or []
    ok = bool(raw)
    for j, w in enumerate(raw):
        try:
            s_, e_ = float(w["s"]), float(w["e"])
        except (KeyError, TypeError, ValueError):
            ok = False
            break
        t = str(w.get("w", "")).strip()
        if t:
            out.append({"id": f"{seg['idx']}:{j}", "w": t, "start": round(s_, 3), "end": round(max(e_, s_), 3),
                        "confidence": w.get("p"), "estimated": False})
    if ok and out:
        return out
    toks = str(seg.get("text", "")).split()
    a, b = float(seg["start"]), float(seg["end"])
    step = (b - a) / len(toks) if toks else 0.0
    return [{"id": f"{seg['idx']}:{j}", "w": t, "start": round(a + j * step, 3), "end": round(a + (j + 1) * step, 3),
             "confidence": None, "estimated": True} for j, t in enumerate(toks)]


def sentences_of(segs: list) -> list:
    """Words grouped into sentences (a sentence ends at . ? ! or at the end of a transcript segment)."""
    out, cur = [], []

    def flush():
        if cur:
            out.append({"id": len(out), "start": cur[0]["start"], "end": cur[-1]["end"],
                        "text": " ".join(w["w"] for w in cur), "words": list(cur)})
            cur.clear()
    for seg in segs:
        for w in words_of(seg):
            cur.append(w)
            if _END.search(w["w"]):
                flush()
        flush()
    return out


def stored_segments(s, transcript_id: int) -> list:
    return [{"idx": g.idx, "start": g.start, "end": g.end, "text": g.text, "words": g.words}
            for g in s.query(TranscriptSegment).filter_by(transcript_id=transcript_id).order_by(TranscriptSegment.idx)]


def latest(s, project_id: int):
    return s.query(Transcript).filter_by(project_id=project_id).order_by(Transcript.id.desc()).first()


def for_project(s, project_id: int) -> dict | None:
    t = latest(s, project_id)
    if t is None:
        return None
    segs = stored_segments(s, t.id)
    sents = sentences_of(segs)
    return {"project_id": project_id, "transcript_id": t.id, "language": t.language, "model": t.model,
            "duration": segs[-1]["end"] if segs else 0.0, "cached": True,
            "word_times": "spoken" if not any(w["estimated"] for x in sents for w in x["words"]) else "partly estimated",
            "sentences": sents}


def reusable_by_hash(s, project_id: int, video: SourceVideo):
    """(segments, info) of another project's transcript of the very same file (same sha256), or None.
    This is what keeps a re-uploaded sermon from being transcribed twice."""
    if not video.sha256:
        return None
    src = (s.query(Transcript).join(SourceVideo, Transcript.video_id == SourceVideo.id)
           .filter(SourceVideo.sha256 == video.sha256, Transcript.project_id != project_id)
           .order_by(Transcript.id.desc()).first())
    if src is None:
        return None
    segs = [{"start": g["start"], "end": g["end"], "text": g["text"], **({"words": g["words"]} if g["words"] else {})}
            for g in stored_segments(s, src.id)]
    return (segs, {"language": src.language, "model": src.model}) if segs else None
