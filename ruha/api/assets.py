from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_
from starlette.background import BackgroundTask

from ruha import handlers
from ruha.api.deps import app_ctx, file_response, get_or_404
from ruha.models import (Approval, AssetVersion, ContentTag, GeneratedAsset, Poster, Project, Reel, SocialPost,
                         SourceVideo)
from ruha.security import ensure_within, sanitize_filename, slugify, temp_file
from ruha.services import assets as svc
from ruha.services import projects as project_svc

router = APIRouter(prefix="/api", tags=["assets"])

LIBRARY_TYPES = {
    "video": None, "reel": ["main_reel"], "short": ["short_reel"], "poster": ["poster"],
    "social": ["social_post"], "image": ["image_reel"], "creative": ["creative"], "thumbnail": ["thumbnail"], "carousel": ["carousel"], "transcript": ["transcript"], "subtitle": ["subtitle", "translation_subtitle"],
    "caption": ["caption"], "audio": ["audio"], "translation": ["translation_video"],
}


class PosterFields(BaseModel):
    headline: str | None = None
    subheadline: str | None = None
    quote: str | None = None


class SocialFields(BaseModel):
    headline: str | None = None
    body: str | None = None
    reference: str | None = None


class SlideFields(BaseModel):
    reference: str | None = None


class DrawIn(BaseModel):
    headline: str | None = None
    body: str | None = None
    reference: str | None = None
    layout: str | None = None


class AssetPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    caption: str | None = None
    hashtags: list | None = None
    poster: PosterFields | None = None
    social_post: SocialFields | None = None
    slide: SlideFields | None = None


class ReviewIn(BaseModel):
    action: str
    reason: str = ""
    comment: str = ""


class RegenerateIn(BaseModel):
    mode: str = "regenerate"       # regenerate (new AI text) | rerender (use current text)
    start: float | None = None
    end: float | None = None
    note: str = ""
    layout: str | None = None


class TagsIn(BaseModel):
    tags: list


class DownloadIn(BaseModel):
    asset_ids: list = []
    video_ids: list = []


def _detail(s, a: GeneratedAsset, ctx=None) -> dict:
    d = svc.serialize(a, s)
    d["tags"] = project_svc.get_tags(s, "asset", a.id)
    if a.kind == "carousel":
        d["slide"] = handlers.carousel_slide_fields(ctx, a) if ctx else {"reference": "", "label": ""}
    d["project_name"] = s.query(Project.name).filter_by(id=a.project_id).scalar()
    d["versions"] = [{"version": v.version, "title": v.title, "caption": v.caption, "description": v.description,
                      "hashtags": json.loads(v.hashtags_json or "[]"), "status": v.status, "reason": v.reason,
                      "created_by": v.created_by, "created_at": v.created_at.isoformat(),
                      "has_file": bool(v.file_path and Path(v.file_path).exists())}
                     for v in a.versions]
    d["reviews"] = [{"id": r.id, "action": r.action, "actor": r.actor, "reason": r.reason, "comment": r.comment,
                     "version": r.asset_version, "at": r.created_at.isoformat()}
                    for r in s.query(Approval).filter_by(asset_id=a.id).order_by(Approval.id.desc())]
    return d


@router.get("/assets")
def list_assets(project_id: int | None = None, kind: str = "", status: str = "", ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        qs = s.query(GeneratedAsset)
        if project_id:
            qs = qs.filter_by(project_id=project_id)
        if kind:
            qs = qs.filter(GeneratedAsset.kind.in_(kind.split(",")))
        if status:
            qs = qs.filter(GeneratedAsset.status.in_(status.split(",")))
        return [svc.serialize(a, s) for a in qs.order_by(GeneratedAsset.slot, GeneratedAsset.id).limit(2000)]


@router.get("/assets/{aid}")
def get_asset(aid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        return _detail(s, get_or_404(s, GeneratedAsset, aid, "Asset"), ctx)


@router.patch("/assets/{aid}")
def patch_asset(aid: int, body: AssetPatch, ctx=Depends(app_ctx)):
    changed_text = False
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        fields = {k: v for k, v in body.model_dump(include={"title", "description", "caption", "hashtags"}).items()
                  if v is not None}
        before = (a.title, a.description)
        svc.edit(ctx, s, a, fields)
        if a.kind == "carousel":                       # a slide's headline and message are its title and description
            changed_text = (a.title, a.description) != before
            if body.slide and body.slide.reference is not None:
                cur = handlers.carousel_slide_fields(ctx, a)
                if body.slide.reference != cur["reference"]:
                    m = dict(a.meta)
                    m["reference"], m["label"] = body.slide.reference, cur["label"]
                    a.metadata_json = json.dumps(m, ensure_ascii=False, default=str)
                    changed_text = True
        if body.poster and a.kind == "poster":
            r = s.query(Poster).filter_by(asset_id=aid).first()
            for k, v in body.poster.model_dump(exclude_none=True).items():
                if getattr(r, k) != v:
                    setattr(r, k, v)
                    changed_text = True
        if body.social_post and a.kind == "social_post":
            r = s.query(SocialPost).filter_by(asset_id=aid).first()
            for k, v in body.social_post.model_dump(exclude_none=True).items():
                if getattr(r, k) != v:
                    setattr(r, k, v)
                    changed_text = True
    if changed_text:
        handlers.rerender_graphic(ctx, aid, reason="text edited")
    else:
        ctx.memory.index_asset(aid)
    with ctx.db.session() as s:
        return _detail(s, s.get(GeneratedAsset, aid), ctx)


@router.post("/assets/{aid}/draw-preview")
def draw_preview(aid: int, body: DrawIn, ctx=Depends(app_ctx)):
    """Draw a poster / social post / carousel slide with text that is not saved yet (a live preview). Changes nothing."""
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        if a.kind not in handlers.GRAPHIC_KINDS:
            raise HTTPException(400, "Only posters, social posts and carousel slides can be previewed")
    over = {k: v for k, v in body.model_dump(exclude={"layout"}).items() if v is not None}
    try:
        path = handlers.preview_graphic(ctx, aid, over, body.layout)
    except Exception as e:
        raise HTTPException(500, f"Could not draw the preview: {str(e)[:200]}")
    r = file_response(ctx, str(path), media_type="image/png")
    r.headers["Cache-Control"] = "no-store"
    return r


@router.post("/assets/{aid}/redraw")
def redraw(aid: int, body: DrawIn, ctx=Depends(app_ctx)):
    """Redraw a poster / social post / carousel slide right now from its text (optionally in another layout) as a new version."""
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        if a.kind not in handlers.GRAPHIC_KINDS:
            raise HTTPException(400, "Only posters, social posts and carousel slides can be redrawn")
    if body.layout not in (None, "classic", "band", "centered"):
        raise HTTPException(400, "Unknown layout")
    handlers.rerender_graphic(ctx, aid, layout=body.layout, reason="layout changed" if body.layout else "redrawn")
    with ctx.db.session() as s:
        return _detail(s, s.get(GeneratedAsset, aid), ctx)


@router.post("/assets/{aid}/review")
def review(aid: int, body: ReviewIn, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        svc.review(ctx, s, a, body.action, body.reason, body.comment)
        status = a.status
    if body.action in ("approve", "reject", "revise"):
        ctx.memory.set_outcome(aid, {"approve": "approved", "reject": "rejected", "revise": "revision_requested"}[body.action])
    with ctx.db.session() as s:
        return _detail(s, s.get(GeneratedAsset, aid), ctx)


@router.post("/assets/{aid}/versions/{version}/restore")
def restore_version(aid: int, version: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        v = s.query(AssetVersion).filter_by(asset_id=aid, version=version).first()
        if not v:
            raise HTTPException(404, "Version not found")
        svc.edit(ctx, s, a, {"title": v.title, "description": v.description, "caption": v.caption,
                             "hashtags": json.loads(v.hashtags_json or "[]")})
    ctx.memory.index_asset(aid)
    with ctx.db.session() as s:
        return _detail(s, s.get(GeneratedAsset, aid), ctx)


@router.post("/assets/{aid}/regenerate", status_code=202)
def regenerate(aid: int, body: RegenerateIn, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        pid, kind = a.project_id, a.kind
        if kind in ("main_reel", "short_reel"):
            r = s.query(Reel).filter_by(asset_id=aid).first()
            start = body.start if body.start is not None else r.start_s
            end = body.end if body.end is not None else r.end_s
            dur = s.query(SourceVideo.duration).filter_by(project_id=pid, is_current=True).scalar() or 0
            if end - start < 3 or start < 0 or (dur and end > dur + 0.5):
                raise HTTPException(400, "Invalid start/end for this video")
            jid = ctx.queue.enqueue("video_rendering", pid, {"asset_id": aid, "start": start, "end": end})
        elif kind == "poster":
            jid = ctx.queue.enqueue("poster_generation", pid, {"asset_id": aid, "layout": body.layout})
        elif kind == "social_post":
            jid = ctx.queue.enqueue("social_post_generation", pid, {"asset_id": aid, "mode": body.mode,
                                                                    "note": body.note, "layout": body.layout})
        elif kind == "image_reel":
            m = dict(a.meta)
            jid = ctx.queue.enqueue("image_reel", pid, {"images": m.get("images"), "hook": a.title,
                                                         "seconds_per_image": m.get("seconds_per_image", 3.5),
                                                         "theme": m.get("visual_theme"), "closing": m.get("closing", ""),
                                                         "language": m.get("language", "English")})
        else:
            raise HTTPException(400, "This asset type cannot be regenerated")
    return {"job_id": jid}


@router.delete("/assets/{aid}")
def delete_asset(aid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        svc.delete_files(ctx, a)
        s.query(ContentTag).filter_by(entity_type="asset", entity_id=aid).delete()
        ctx.audit(s, "asset", aid, "delete", kind=a.kind)
        s.delete(a)
    ctx.memory.delete(asset_id=aid)
    return {"deleted": aid}


@router.put("/assets/{aid}/tags")
def set_tags(aid: int, body: TagsIn, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        get_or_404(s, GeneratedAsset, aid, "Asset")
        return {"tags": project_svc.set_tags(s, "asset", aid, body.tags)}


@router.get("/assets/{aid}/file")
def asset_file(aid: int, download: bool = False, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        path, name = a.file_path, f"{slugify(a.title or a.kind)}{Path(a.file_path or '').suffix}"
    return file_response(ctx, path, filename=name, download=download)


@router.get("/assets/{aid}/variants/{fmt}/file")
def asset_variant_file(aid: int, fmt: str, download: bool = False, ctx=Depends(app_ctx)):
    """The same reel in another format (4:5, 1:1, 16:9) chosen in Settings; "main" or the reel's own format = the main file."""
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        meta = a.meta
        var = (meta.get("variants") or {}).get(fmt)
        if fmt == "main" or fmt == meta.get("format"):
            path, tag = a.file_path, meta.get("format") or "main"
        elif isinstance(var, dict):
            path, tag = var.get("file"), fmt
        else:
            raise HTTPException(404, f"This reel has no {fmt} version")
        name = f"{slugify(a.title or a.kind)}-{tag.replace(':', 'x')}{Path(path or '').suffix}"
    return file_response(ctx, path, filename=name, download=download)


@router.get("/assets/{aid}/preview")
def asset_preview(aid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        path = a.preview_path or (a.file_path if (a.mime or "").startswith("image/") else None)
    return file_response(ctx, path)


@router.get("/assets/{aid}/text")
def asset_text(aid: int, ctx=Depends(app_ctx)):
    """Return a text-like asset (transcript, subtitles, captions) for inline preview."""
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Asset")
        path = a.file_path
    p = ensure_within(path or "", ctx.settings.allowed_roots)
    if p.suffix.lower() not in (".txt", ".srt", ".md", ".json", ".vtt") or not p.exists():
        raise HTTPException(400, "Not a text asset")
    return {"text": p.read_text(encoding="utf-8", errors="replace")[:500_000]}


@router.get("/assets/{aid}/versions/{version}/file")
def version_file(aid: int, version: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        v = s.query(AssetVersion).filter_by(asset_id=aid, version=version).first()
        if not v:
            raise HTTPException(404, "Version not found")
        path = v.file_path
    return file_response(ctx, path)


# --------------------------------- library -----------------------------------
@router.get("/library")
def library(type: str = "", q: str = "", tag: str = "", status: str = "", project_id: int | None = None,
            sort: str = "created", order: str = "desc", limit: int = 60, offset: int = 0, ctx=Depends(app_ctx)):
    items = []
    with ctx.db.session() as s:
        names = {i: n for i, n in s.query(Project.id, Project.name)}
        tag_rows = s.query(ContentTag.entity_type, ContentTag.entity_id, ContentTag.tag).all()
        tags = {}
        for et, eid, t in tag_rows:
            tags.setdefault((et, eid), []).append(t)
        if type != "video":
            qs = s.query(GeneratedAsset)
            if type:
                kinds = LIBRARY_TYPES.get(type)
                if kinds is None:
                    raise HTTPException(400, f"Unknown type '{type}'")
                qs = qs.filter(GeneratedAsset.kind.in_(kinds))
            if status:
                qs = qs.filter(GeneratedAsset.status == status)
            if project_id:
                qs = qs.filter(GeneratedAsset.project_id == project_id)
            if q:
                like = f"%{q}%"
                qs = qs.filter(or_(GeneratedAsset.title.ilike(like), GeneratedAsset.caption.ilike(like),
                                   GeneratedAsset.description.ilike(like)))
            for a in qs.all():
                at = tags.get(("asset", a.id), [])
                if tag and tag.lower() not in at:
                    continue
                d = svc.serialize(a)
                d.update(entity="asset", tags=at, project_name=names.get(a.project_id))
                items.append(d)
        if (not type or type == "video") and not status:
            qv = s.query(SourceVideo)
            if project_id:
                qv = qv.filter_by(project_id=project_id)
            if q:
                qv = qv.filter(SourceVideo.original_name.ilike(f"%{q}%"))
            for v in qv.all():
                if tag and tag.lower() not in tags.get(("project", v.project_id), []):
                    continue
                items.append({"entity": "video", "id": v.id, "project_id": v.project_id, "kind": "video",
                              "title": v.original_name, "size_bytes": v.size_bytes, "status": "source",
                              "version": 1, "created_at": v.created_at.isoformat(), "updated_at": v.created_at.isoformat(),
                              "has_preview": bool(v.thumbnail_path), "duration": v.duration,
                              "project_name": names.get(v.project_id), "tags": tags.get(("project", v.project_id), []),
                              "caption": "", "description": "", "hashtags": [], "meta": {}})
    key = {"created": "created_at", "name": "title", "size": "size_bytes", "updated": "updated_at"}.get(sort, "created_at")
    items.sort(key=lambda x: (x.get(key) is None, x.get(key) or ""), reverse=(order == "desc"))
    total = len(items)
    return {"total": total, "items": items[max(0, offset): max(0, offset) + max(1, min(limit, 500))]}


@router.post("/library/download")
def bulk_download(body: DownloadIn, ctx=Depends(app_ctx)):
    if not (body.asset_ids or body.video_ids):
        raise HTTPException(400, "Nothing selected")
    tmp = temp_file(ctx.settings.tmp_dir, ".zip")
    added = 0
    with ctx.db.session() as s, zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for a in s.query(GeneratedAsset).filter(GeneratedAsset.id.in_(body.asset_ids or [-1])):
            if a.file_path and Path(a.file_path).is_file():
                p = ensure_within(a.file_path, ctx.settings.allowed_roots)
                pname = s.query(Project.name).filter_by(id=a.project_id).scalar() or "project"
                z.write(p, f"{slugify(pname)}/{a.kind}_{a.id}_{sanitize_filename(p.name)}")
                added += 1
        for v in s.query(SourceVideo).filter(SourceVideo.id.in_(body.video_ids or [-1])):
            if Path(v.path).is_file():
                p = ensure_within(v.path, ctx.settings.allowed_roots)
                pname = s.query(Project.name).filter_by(id=v.project_id).scalar() or "project"
                z.write(p, f"{slugify(pname)}/video_{v.id}_{sanitize_filename(p.name)}")
                added += 1
    if not added:
        tmp.unlink(missing_ok=True)
        raise HTTPException(404, "None of the selected files exist on disk")
    return FileResponse(tmp, filename="ruha-library.zip", media_type="application/zip",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))
