from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_

from ruha.api.deps import app_ctx, file_response, get_or_404
from ruha.config import JOB_TYPES, PROJECT_STATUSES
from ruha.models import (AuditLog, ContentTag, GeneratedAsset, ProcessingJob, Project, SourceVideo, Transcript,
                         TranscriptSegment, Translation)
from ruha.services import assets as asset_svc
from ruha.services import instructions as instr_svc
from ruha.services import projects as svc
from ruha.services import selection

router = APIRouter(prefix="/api", tags=["projects"])


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    content_type: str = "sermon"
    tags: list = []


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    content_type: str | None = None
    tags: list | None = None
    status: str | None = None


class ProcessIn(BaseModel):
    steps: list | None = None
    translations: list = []
    language: str | None = None          # subtitle language for the reels (default English)


class JobIn(BaseModel):
    job_type: str
    params: dict = {}


SORTS = {"name": Project.name, "created": Project.created_at, "updated": Project.updated_at,
         "status": Project.status}


@router.get("/projects")
def list_projects(q: str = "", status: str = "", content_type: str = "", tag: str = "",
                  created_from: str = "", created_to: str = "", sort: str = "created", order: str = "desc",
                  include_archived: bool = False, limit: int = 100, offset: int = 0, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        qs = s.query(Project)
        if status:
            qs = qs.filter(Project.status == status)
        elif not include_archived:
            qs = qs.filter(Project.status != "archived")
        if content_type:
            qs = qs.filter(Project.content_type == content_type)
        if q:
            like = f"%{q}%"
            qs = qs.filter(or_(Project.name.ilike(like), Project.description.ilike(like),
                               Project.id.in_(s.query(ContentTag.entity_id).filter(
                                   ContentTag.entity_type == "project", ContentTag.tag.ilike(like)))))
        if tag:
            qs = qs.filter(Project.id.in_(s.query(ContentTag.entity_id).filter(
                ContentTag.entity_type == "project", ContentTag.tag == tag.lower())))
        try:
            if created_from:
                qs = qs.filter(Project.created_at >= datetime.fromisoformat(created_from))
            if created_to:
                qs = qs.filter(Project.created_at <= datetime.fromisoformat(created_to + ("T23:59:59" if len(created_to) == 10 else "")))
        except ValueError:
            raise HTTPException(400, "Dates must be ISO formatted (YYYY-MM-DD)")
        total = qs.count()
        col = SORTS.get(sort, Project.created_at)
        qs = qs.order_by(col.asc() if order == "asc" else col.desc())
        rows = qs.offset(max(0, offset)).limit(max(1, min(limit, 500))).all()
        return {"total": total, "items": [svc.serialize(s, p) for p in rows]}


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = svc.create(ctx, s, body.name, body.description, body.content_type, body.tags)
        s.flush()
        return svc.serialize(s, p)


@router.get("/projects/{pid}")
def get_project(pid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        d = svc.serialize(s, p)
        d["videos"] = [svc.video_dict(v) for v in s.query(SourceVideo).filter_by(project_id=pid).order_by(SourceVideo.id.desc())]
        d["jobs"] = [_job_brief(j) for j in s.query(ProcessingJob).filter_by(project_id=pid).order_by(ProcessingJob.id.desc()).limit(30)]
        d["translations"] = [{"id": t.id, "target": t.target_language, "dubbed": t.dubbed, "status": t.status,
                              "video_asset_id": t.video_asset_id, "srt_asset_id": t.srt_asset_id,
                              "audio_asset_id": t.audio_asset_id} for t in s.query(Translation).filter_by(project_id=pid)]
        return d


def _job_brief(j):
    return {"id": j.id, "job_type": j.job_type, "status": j.status, "progress": j.progress, "step": j.step,
            "error": j.error, "created_at": j.created_at.isoformat(),
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None}


@router.patch("/projects/{pid}")
def patch_project(pid: int, body: ProjectPatch, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        if body.name is not None:
            p.name = body.name.strip()
        if body.description is not None:
            p.description = body.description
        if body.content_type is not None:
            p.content_type = body.content_type
        if body.tags is not None:
            svc.set_tags(s, "project", pid, body.tags)
        if body.status is not None:
            if body.status == "archived":
                svc.archive(ctx, s, p)
            else:
                svc.set_status(ctx, s, p, body.status)
        ctx.audit(s, "project", pid, "update", fields=[k for k, v in body.model_dump().items() if v is not None])
        s.flush()
        return svc.serialize(s, p)


@router.delete("/projects/{pid}")
def delete_project(pid: int, confirm: bool = Query(False), ctx=Depends(app_ctx)):
    if not confirm:
        raise HTTPException(400, "Deleting a project is permanent - pass confirm=true")
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        info = svc.delete(ctx, s, p)
    clean = svc.finish_delete(ctx, info)
    from ruha.services import brand as brand_svc
    brand_svc.forget_project_kit(ctx, pid)            # a reused project id must not inherit the old logo/texts
    return {"deleted": pid, "files_removed": clean}


@router.post("/projects/{pid}/duplicate", status_code=201)
def duplicate(pid: int, include_assets: bool = False, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        new = svc.duplicate(ctx, s, p, include_assets)
        s.flush()
        return svc.serialize(s, new)


@router.post("/projects/{pid}/archive")
def archive(pid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        svc.archive(ctx, s, p)
        return svc.serialize(s, p)


@router.post("/projects/{pid}/unarchive")
def unarchive(pid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        svc.unarchive(ctx, s, p)
        return svc.serialize(s, p)


@router.get("/projects/{pid}/history")
def history(pid: int, ctx=Depends(app_ctx)):
    import json
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Project")
        asset_ids = [a for a, in s.query(GeneratedAsset.id).filter_by(project_id=pid)]
        rows = s.query(AuditLog).filter(or_(
            (AuditLog.entity_type == "project") & (AuditLog.entity_id == pid),
            (AuditLog.entity_type == "asset") & (AuditLog.entity_id.in_(asset_ids or [-1])))) \
            .order_by(AuditLog.id.desc()).limit(200).all()
        return [{"id": r.id, "entity": r.entity_type, "entity_id": r.entity_id, "action": r.action,
                 "actor": r.actor, "detail": json.loads(r.detail_json) if r.detail_json else {},
                 "at": r.created_at.isoformat()} for r in rows]


@router.get("/projects/{pid}/thumbnail")
def thumbnail(pid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        path = p.thumbnail_path
    return file_response(ctx, path)


# ---------------------------------- videos -----------------------------------
@router.post("/projects/{pid}/videos", status_code=201)
def upload_video(pid: int, file: UploadFile = File(...), replace: bool = Form(False), ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        if not replace and s.query(SourceVideo).filter_by(project_id=pid, is_current=True).count():
            raise HTTPException(409, "This project already has a video. Use replace=true to replace it (the old file is kept).")
        tmp = svc.save_upload(ctx, p, file.file, file.filename or "video.mp4")
        v = svc.attach_video(ctx, s, p, tmp, file.filename or "video.mp4", replace=replace)
        s.flush()
        dups = svc.duplicates_of(s, v)
        return {"video": svc.video_dict(v), "duplicates": dups}


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024


def _image_files(ws: Path | None) -> list:
    d = (ws / "images") if ws else None
    return sorted(f for f in d.glob("*") if f.is_file() and f.suffix.lower() in IMAGE_EXTS) if d and d.exists() else []


@router.get("/projects/{pid}/images")
def list_images(pid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        return [{"name": f.name, "size": f.stat().st_size} for f in _image_files(Path(p.workspace_dir))]


@router.post("/projects/{pid}/images", status_code=201)
def upload_images(pid: int, files: list[UploadFile] = File(...), ctx=Depends(app_ctx)):
    from PIL import Image, UnidentifiedImageError
    from ruha.security import sanitize_filename
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        d = Path(p.workspace_dir) / "images"
    d.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = sanitize_filename(f.filename or "image.jpg", "image.jpg")
        if Path(name).suffix.lower() not in IMAGE_EXTS:
            raise HTTPException(400, f"'{name}' is not a supported image (use JPG, PNG or WEBP)")
        data = f.file.read(MAX_IMAGE_BYTES + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(400, f"'{name}' is empty or larger than 25 MB")
        dest, n = d / name, 1
        while dest.exists():
            dest = d / f"{Path(name).stem}_{n}{Path(name).suffix}"
            n += 1
        dest.write_bytes(data)
        try:
            with Image.open(dest) as im:
                im.verify()
        except (UnidentifiedImageError, OSError):
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"'{name}' is not a valid image")
        saved.append(dest.name)
    return {"saved": saved}


@router.get("/projects/{pid}/images/{name}")
def get_image(pid: int, name: str, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        path = Path(p.workspace_dir) / "images" / Path(name).name
    return file_response(ctx, str(path))


@router.delete("/projects/{pid}/images/{name}")
def delete_image(pid: int, name: str, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        path = Path(p.workspace_dir) / "images" / Path(name).name
    if not path.is_file():
        raise HTTPException(404, "Image not found")
    path.unlink()
    return {"deleted": name}


class ImportUrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    name: str = ""
    content_type: str = "sermon"
    process: bool = True
    language: str | None = None


@router.post("/import-url", status_code=202)
def import_url(body: ImportUrlIn, ctx=Depends(app_ctx)):
    """Create a project from a video link: download it, then run the full content pipeline."""
    from ruha.ai import ytdl
    try:
        url = ytdl.validate_url(body.url)
    except ytdl.DownloadError as e:
        raise HTTPException(400, str(e))
    with ctx.db.session() as s:
        p = svc.create(ctx, s, body.name.strip() or "Imported video", f"Imported from {url}", body.content_type, [])
        s.flush()
        pid = p.id
    jid = ctx.queue.enqueue("video_download", pid, {"url": url, "process": body.process, "language": body.language,
                                                    "rename": not body.name.strip()})
    return {"project_id": pid, "job_id": jid}


@router.delete("/videos/{vid}")
def delete_video(vid: int, confirm: bool = Query(False), ctx=Depends(app_ctx)):
    if not confirm:
        raise HTTPException(400, "Pass confirm=true to delete the video file")
    with ctx.db.session() as s:
        v = get_or_404(s, SourceVideo, vid, "Video")
        p = s.get(Project, v.project_id)
        svc.delete_video(ctx, s, v, p)
    return {"deleted": vid}


@router.get("/videos/{vid}/stream")
def stream_video(vid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        v = get_or_404(s, SourceVideo, vid, "Video")
        path = v.path
    return file_response(ctx, path)


@router.get("/videos/{vid}/thumbnail")
def video_thumb(vid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        v = get_or_404(s, SourceVideo, vid, "Video")
        path = v.thumbnail_path
    return file_response(ctx, path)


@router.get("/videos/{vid}/download")
def download_video(vid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        v = get_or_404(s, SourceVideo, vid, "Video")
        path, name = v.path, v.original_name
    return file_response(ctx, path, filename=name, download=True)


# ------------------------------- processing ----------------------------------
@router.post("/projects/{pid}/process", status_code=202)
def process(pid: int, body: ProcessIn | None = None, ctx=Depends(app_ctx)):
    body = body or ProcessIn()
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Project")
        if not svc.current_video(s, pid):
            raise HTTPException(400, "Upload a video before processing")
    ids = ctx.queue.enqueue_pipeline(pid, body.steps, body.translations, body.language)
    return {"job_ids": ids}


class InstructionsIn(BaseModel):
    reels: str = ""      # one reel per line: "12:30-13:45 main - comment"
    posters: str = ""    # free-text instructions for the poster
    topic: str | None = None   # "Find a moment about ..." (None = keep the saved one)
    caption_template: str | None = None   # stacked | wordpop | karaoke | classic ("" = the Settings default; None = keep)


def _instr_ctx(ctx, pid):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        v = svc.current_video(s, pid)
        return Path(p.workspace_dir), (v.duration if v else None)


@router.get("/projects/{pid}/instructions")
def get_instructions(pid: int, ctx=Depends(app_ctx)):
    ws, duration = _instr_ctx(ctx, pid)
    d = instr_svc.load(ws)
    return {"reels": d["reels"], "posters": d["posters"], "topic": d["topic"], "caption_template": d["caption_template"],
            "video_seconds": duration}


@router.put("/projects/{pid}/instructions")
def put_instructions(pid: int, body: InstructionsIn, ctx=Depends(app_ctx)):
    """Save the reels/posters boxes. Reel times are checked against the video length and any problem
    is reported (400) before anything is saved."""
    ws, duration = _instr_ctx(ctx, pid)
    rs = selection.reel_settings(ctx.config.all())
    try:
        entries, warnings = instr_svc.parse_reels(body.reels, duration, (rs["min_s"], rs["max_s"]))
        instr_svc.save(ws, body.reels, body.posters, topic=body.topic, caption_template=body.caption_template)
    except instr_svc.InstructionError as e:
        raise HTTPException(400, str(e))
    return {"reels": body.reels.strip(), "posters": body.posters.strip(), "topic": instr_svc.load(ws)["topic"],
            "caption_template": instr_svc.load(ws)["caption_template"], "warnings": warnings,
            "parsed": [{"start": e["start"], "end": e["end"], "category": e["category"], "note": e["note"]} for e in entries]}


@router.post("/projects/{pid}/jobs", status_code=202)
def create_job(pid: int, body: JobIn, ctx=Depends(app_ctx)):
    if body.job_type not in JOB_TYPES:
        raise HTTPException(400, f"job_type must be one of {JOB_TYPES}")
    if body.params.get("clone") and not (body.job_type == "dubbing" and body.params.get("clone_consent") is True):
        raise HTTPException(400, "Voice cloning requires a dubbing job and confirmation that you have the "
                                 "speaker's permission (clone_consent)")
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Project")
        if body.job_type not in ("video_rendering", "image_reel") and not svc.current_video(s, pid):
            raise HTTPException(400, "Upload a video first")
        if body.params.get("asset_id") is not None and body.job_type in ("translation", "dubbing"):
            try:
                asset_svc.reel_source(s, pid, body.params["asset_id"])
            except ValueError as e:
                raise HTTPException(400, str(e))
        if body.job_type == "image_reel" and not _image_files(Path(p0.workspace_dir) if (p0 := s.get(Project, pid)) else None):
            raise HTTPException(400, "Upload at least one image first")
    return {"job_id": ctx.queue.enqueue(body.job_type, pid, body.params)}


@router.get("/projects/{pid}/transcript")
def transcript(pid: int, q: str = "", ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Project")
        t = s.query(Transcript).filter_by(project_id=pid).order_by(Transcript.id.desc()).first()
        if not t:
            return {"exists": False}
        segs = s.query(TranscriptSegment).filter_by(transcript_id=t.id).order_by(TranscriptSegment.idx)
        if q:
            segs = segs.filter(TranscriptSegment.text.ilike(f"%{q}%"))
        return {"exists": True, "id": t.id, "language": t.language, "model": t.model, "summary": t.summary,
                "themes": t.themes, "quotes": t.quotes, "plan": t.plan,
                "segments": [{"idx": g.idx, "start": g.start, "end": g.end, "text": g.text} for g in segs.limit(5000)]}
