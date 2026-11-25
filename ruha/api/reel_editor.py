"""Reel Editor API: the word-level transcript, the owner's reels (ordered segments), previews, approval and dubbing of approved reels."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ruha.api.deps import app_ctx, file_response, get_or_404
from ruha.models import EditorReel, Project
from ruha.services import hooks, reel_editor as ed, reel_render, transcripts

router = APIRouter(prefix="/api", tags=["reel-editor"])


def _guard(fn, *a, **k):
    try:
        return fn(*a, **k)
    except ed.EditorError as e:
        raise HTTPException(400, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


# ------------------------------------------------------------------ transcript
@router.get("/sermons/{pid}/transcript")
def get_transcript(pid: int, ctx=Depends(app_ctx)):
    """Every sentence of the sermon with every word's start, end and confidence. Read from the stored transcript."""
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Sermon")
        t = transcripts.for_project(s, pid)
    if t is None:
        raise HTTPException(404, "This sermon has no transcript yet - run transcription first")
    return t


@router.get("/sermons/{pid}/suggested-reels")
def suggested(pid: int, ctx=Depends(app_ctx)):
    """The planner's reel suggestions - a starting point the owner can edit, never a lock."""
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Sermon")
        t = transcripts.latest(s, pid)
        plan = (t.plan if t else None) or {}
    out = []
    for key in ("main_reels", "short_reels"):
        for i, c in enumerate(plan.get(key) or [], 1):
            out.append({"id": f"{key[:-6]}_{i}", "start": c["start"], "end": c["end"], "hook": c.get("hook", ""),
                        "reason": c.get("reason", ""), "score": c.get("score"), "category": key[:-6]})
    return {"suggestions": out}


# ------------------------------------------------------------------ reels
class ReelIn(BaseModel):
    title: str = ""
    segments: list = []
    options: dict | None = None
    aspect: str = "9:16"
    language: str = "English"
    suggestion: str | None = None            # "main_1" / "short_2": start from the planner's choice


class ReelPatch(BaseModel):
    title: str | None = None
    language: str | None = None
    aspect: str | None = None
    segments: list | None = None
    snap: bool = True
    options: dict | None = None
    caption_edits: dict | None = None


@router.get("/sermons/{pid}/reels")
def list_reels(pid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Sermon")
        rows = s.query(EditorReel).filter_by(project_id=pid).order_by(EditorReel.id.desc()).all()
        return {"items": [ed.as_dict(r) for r in rows]}


@router.post("/sermons/{pid}/reels", status_code=201)
def create_reel(pid: int, body: ReelIn, ctx=Depends(app_ctx)):
    segments, title, source = body.segments, body.title, "manual"
    if body.suggestion:
        sug = {x["id"]: x for x in suggested(pid, ctx)["suggestions"]}.get(body.suggestion)
        if sug is None:
            raise HTTPException(404, "That suggestion no longer exists")
        segments, title, source = [{"start": sug["start"], "end": sug["end"]}], title or sug["hook"], f"plan:{body.suggestion}"
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Sermon")
        r = _guard(ed.create, s, pid, title, segments, body.options, source, body.language, body.aspect)
        return ed.as_dict(r)


@router.get("/editor/reels/{rid}")
def get_reel(rid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        return ed.as_dict(_guard(ed.get, s, rid))


@router.patch("/editor/reels/{rid}")
def patch_reel(rid: int, body: ReelPatch, ctx=Depends(app_ctx)):
    patch = body.model_dump(exclude_unset=True)
    with ctx.db.session() as s:
        return ed.as_dict(_guard(ed.update, s, rid, patch))


@router.post("/editor/reels/{rid}/duplicate", status_code=201)
def duplicate_reel(rid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        return ed.as_dict(_guard(ed.duplicate, s, rid))


@router.delete("/editor/reels/{rid}")
def delete_reel(rid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        r = _guard(ed.get, s, rid)
        s.delete(r)
    return {"deleted": rid}


class VersionIn(BaseModel):
    note: str = Field("", max_length=200)


@router.post("/editor/reels/{rid}/versions", status_code=201)
def save_version(rid: int, body: VersionIn, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        return _guard(ed.save_version, s, rid, body.note)


@router.get("/editor/reels/{rid}/versions")
def list_versions(rid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        return {"items": _guard(ed.versions, s, rid)}


@router.post("/editor/reels/{rid}/versions/{n}/restore")
def restore_version(rid: int, n: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        return ed.as_dict(_guard(ed.restore_version, s, rid, n))


# ------------------------------------------------------------------ render, review, approve
@router.post("/editor/reels/{rid}/preview", status_code=202)
def render_preview(rid: int, ctx=Depends(app_ctx)):
    """A small, fast draft in the background queue. When it is done the reel is Ready for review."""
    with ctx.db.session() as s:
        r = _guard(ed.get, s, rid)
        if not r.segments:
            raise HTTPException(400, "Add at least one part of the sermon to the reel first")
        pid = r.project_id
    return {"job_id": ctx.queue.enqueue("reel_render", pid, {"reel_id": rid, "mode": "preview"})}


@router.post("/editor/reels/{rid}/approve", status_code=202)
def approve(rid: int, ctx=Depends(app_ctx)):
    """Ready for review -> Approved, and the final render starts. A reel that was edited since its preview must be previewed again."""
    with ctx.db.session() as s:
        r = _guard(ed.get, s, rid)
        if r.status not in ("ready",):
            raise HTTPException(409, "Render a preview and review it first - only a reel that is Ready for review can be approved"
                                if r.status == "draft" else f"This reel is already {r.status}")
        r.status = "approved"
        pid = r.project_id
    return {"job_id": ctx.queue.enqueue("reel_render", pid, {"reel_id": rid, "mode": "final"}), "status": "approved"}


class TranslateIn(BaseModel):
    target: str = "Hindi"
    dub: bool = False
    voice: str = "Female"
    keep_background: bool = True
    burn: bool = False


@router.post("/editor/reels/{rid}/translate", status_code=202)
def translate(rid: int, body: TranslateIn, ctx=Depends(app_ctx)):
    """Dub or translate the reel - only once it is Approved and its final render exists (it dubs that edited cut)."""
    with ctx.db.session() as s:
        r = _guard(ed.get, s, rid)
        if r.status not in ("approved", "rendered"):
            raise HTTPException(409, "Only an Approved reel can be dubbed or translated. Approve it first.")
        if not r.asset_id:
            raise HTTPException(409, "The final render is not finished yet - try again when it is done.")
        pid, asset_id = r.project_id, r.asset_id
    params = {"target": body.target, "dub": body.dub, "voice": body.voice, "keep_background": body.keep_background,
              "burn": body.burn, "asset_id": asset_id, "editor_reel_id": rid}
    return {"job_id": ctx.queue.enqueue("dubbing" if body.dub else "translation", pid, params)}


@router.get("/editor/reels/{rid}/file/{kind}")
def reel_file(rid: int, kind: str, ctx=Depends(app_ctx)):
    from ruha.models import GeneratedAsset
    with ctx.db.session() as s:
        r = _guard(ed.get, s, rid)
        if kind == "preview":
            path = r.preview_path
        elif kind == "final":
            a = s.get(GeneratedAsset, r.asset_id) if r.asset_id else None
            path = a.file_path if a else None
        elif kind == "thumbnail":
            path = r.options.get("thumbnail", {}).get("path")
        else:
            raise HTTPException(404, "Unknown file")
    return file_response(ctx, path)


# ------------------------------------------------------------------ thumbnail, hook ideas, post drafts
class ThumbIn(BaseModel):
    time: float = Field(0.0, ge=0)
    text: str = Field("", max_length=140)


@router.post("/editor/reels/{rid}/thumbnail")
def make_thumbnail(rid: int, body: ThumbIn, ctx=Depends(app_ctx)):
    """A frame of the reel (the preview, or the final if it exists) with optional text, saved with the reel."""
    from ruha.models import GeneratedAsset
    with ctx.db.session() as s:
        r = _guard(ed.get, s, rid)
        src = r.preview_path
        if r.asset_id:
            a = s.get(GeneratedAsset, r.asset_id)
            src = a.file_path if a and a.file_path and Path(a.file_path).exists() else src
        if not src or not Path(src).exists():
            raise HTTPException(409, "Render a preview first - the thumbnail is a frame of it")
        ws = Path(get_or_404(s, Project, r.project_id, "Sermon").workspace_dir)
        out = ws / "editor" / f"reel_{rid}_thumb.png"
        try:
            reel_render.thumbnail_from(Path(src), body.time, out, body.text)
        except Exception as e:
            raise HTTPException(500, f"Could not make the thumbnail: {str(e)[:200]}")
        r.options_json = __import__("json").dumps(ed.merged_options(r.options, {"thumbnail": {
            "time": body.time, "text": body.text, "path": str(out)}}), ensure_ascii=False)
        return {"path": str(out)}


def _reel_text(s, r) -> str:
    t = transcripts.latest(s, r.project_id)
    return ed.reel_text(ed.as_dict(r), transcripts.stored_segments(s, t.id)) if t else ""


@router.get("/editor/reels/{rid}/hook-suggestions")
def hook_suggestions(rid: int, ctx=Depends(app_ctx)):
    """Opening-line ideas from the reel's own words (and the planner's hook for the same part of the sermon)."""
    with ctx.db.session() as s:
        r = _guard(ed.get, s, rid)
        text = _reel_text(s, r)
        t = transcripts.latest(s, r.project_id)
        plan = (t.plan if t else None) or {}
        mine = [(g.start, g.end) for g in r.segments]
    out = []
    for c in (plan.get("main_reels") or []) + (plan.get("short_reels") or []):
        if c.get("hook") and any(min(c["end"], b) - max(c["start"], a) > 0 for a, b in mine):
            out.append(c["hook"])
    for sent in hooks.sentences(text)[:6]:
        h = hooks.tidy_hook(sent, 8)
        if len(h.split()) >= 3:
            out.append(h)
    seen, res = set(), []
    for h in out:
        if h.lower() not in seen:
            seen.add(h.lower())
            res.append(h)
    return {"suggestions": res[:6]}


@router.post("/editor/reels/{rid}/posts/draft")
def posts_draft(rid: int, ctx=Depends(app_ctx)):
    """Starting post text for each platform, from the reel's own words. Nothing is saved until the owner saves the reel."""
    with ctx.db.session() as s:
        r = _guard(ed.get, s, rid)
        t = transcripts.latest(s, r.project_id)
        tags = ((t.plan if t else None) or {}).get("hashtags") or []
        return ed.posts_draft(r.title, _reel_text(s, r), tags)
