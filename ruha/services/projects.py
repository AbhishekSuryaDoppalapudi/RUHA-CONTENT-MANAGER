"""Projects, workspaces and source-video management."""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import sys
import time
import uuid
from pathlib import Path

from ruha.config import PROJECT_STATUSES, VIDEO_EXTENSIONS
from ruha.models import (ContentTag, GeneratedAsset, MemoryReference, ProcessingJob, Project, SourceVideo,
                         Transcript, utcnow)
from ruha.security import ensure_within, sanitize_filename, slugify
from ruha.services import assets as asset_svc
from ruha.services import media

SUBDIRS = ["source", "audio", "transcript", "reels", "subtitles", "posters", "social", "translations",
           "thumbs", "versions"]


log = logging.getLogger("ruha.projects")


class ProjectError(ValueError):
    pass


def remove_tree(path: Path, attempts: int = 4, delay: float = 0.5) -> list:
    """Delete a folder tree even when Windows/OneDrive/an indexer briefly holds a file or folder open: read-only
    files are made writable, and a failed pass is retried after a short wait. Returns what is still left ([] = gone).

    (Deleting a project used to leave its empty folders behind: the files went but ``shutil.rmtree`` failed on the
    folders themselves, the error was swallowed and nothing retried.)"""
    path = Path(path)

    def fix(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    for i in range(attempts):
        if not path.exists():
            return []
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=lambda f, p, e: fix(f, p, e))
            else:
                shutil.rmtree(path, onerror=lambda f, p, e: fix(f, p, e))
        except OSError as e:
            log.warning("Removing %s: %s (try %d of %d)", path, e, i + 1, attempts)
        if not path.exists():
            return []
        time.sleep(delay * (i + 1))
    return sorted(str(x) for x in path.rglob("*"))[:20] or [str(path)]


def workspace_of(project: Project) -> Path:
    return Path(project.workspace_dir)


def create(ctx, s, name: str, description: str = "", content_type: str = "sermon", tags=None) -> Project:
    name = (name or "").strip()
    if not name:
        raise ProjectError("Project name is required")
    p = Project(name=name[:200], description=description or "", content_type=content_type or "sermon",
                workspace_dir="pending")
    s.add(p)
    s.flush()
    ws = ctx.settings.projects_dir / f"{p.id:05d}-{slugify(name)}"
    for d in SUBDIRS:
        (ws / d).mkdir(parents=True, exist_ok=True)
    p.workspace_dir = str(ws)
    set_tags(s, "project", p.id, tags or [])
    ctx.audit(s, "project", p.id, "create", name=name)
    return p


def set_tags(s, entity_type: str, entity_id: int, tags: list) -> list:
    clean = sorted({t.strip().lower()[:60] for t in tags if t and t.strip()})
    s.query(ContentTag).filter_by(entity_type=entity_type, entity_id=entity_id).delete()
    for t in clean:
        s.add(ContentTag(entity_type=entity_type, entity_id=entity_id, tag=t))
    return clean


def get_tags(s, entity_type: str, entity_id: int) -> list:
    return [t.tag for t in s.query(ContentTag).filter_by(entity_type=entity_type, entity_id=entity_id)
            .order_by(ContentTag.tag)]


def set_status(ctx, s, p: Project, status: str):
    if status not in PROJECT_STATUSES:
        raise ProjectError(f"Invalid status '{status}'")
    if status != p.status:
        ctx.audit(s, "project", p.id, "status", old=p.status, new=status)
        p.status = status


def archive(ctx, s, p: Project):
    if p.status != "archived":
        p.status_before_archive = p.status
        p.status = "archived"
        p.archived_at = utcnow()
        ctx.audit(s, "project", p.id, "archive")


def unarchive(ctx, s, p: Project):
    if p.status == "archived":
        p.status = p.status_before_archive or "draft"
        p.archived_at = None
        ctx.audit(s, "project", p.id, "unarchive")


def duplicate(ctx, s, p: Project, include_assets: bool = False) -> Project:
    """Copy the project record, tags and the source video (assets optional)."""
    new = create(ctx, s, f"{p.name} (copy)", p.description, p.content_type, get_tags(s, "project", p.id))
    for v in s.query(SourceVideo).filter_by(project_id=p.id, is_current=True):
        src = Path(v.path)
        if src.exists():
            dst = workspace_of(new) / "source" / src.name
            shutil.copy2(src, dst)
            nv = SourceVideo(project_id=new.id, original_name=v.original_name, path=str(dst), sha256=v.sha256,
                             size_bytes=v.size_bytes, duration=v.duration, width=v.width, height=v.height,
                             fps=v.fps, video_codec=v.video_codec, audio_tracks=v.audio_tracks,
                             audio_codec=v.audio_codec)
            s.add(nv)
            s.flush()
            if v.thumbnail_path and Path(v.thumbnail_path).exists():
                th = workspace_of(new) / "thumbs" / Path(v.thumbnail_path).name
                shutil.copy2(v.thumbnail_path, th)
                nv.thumbnail_path = str(th)
                new.thumbnail_path = str(th)
            new.status = "uploaded"
    if include_assets:
        for a in s.query(GeneratedAsset).filter_by(project_id=p.id).all():
            if not a.file_path or not Path(a.file_path).exists():
                continue
            sub = Path(a.file_path).parent.name
            dst_dir = workspace_of(new) / sub
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / Path(a.file_path).name
            shutil.copy2(a.file_path, dst)
            asset_svc.register(ctx, s, new.id, a.kind, dst, slot=a.slot, title=a.title, description=a.description,
                               caption=a.caption, hashtags=a.hashtags, meta=a.meta)
    ctx.audit(s, "project", new.id, "duplicate", source=p.id)
    return new


def delete(ctx, s, p: Project) -> dict:
    """Delete the DB records (cascade) and the project's workspace folder."""
    pid, ws = p.id, workspace_of(p)
    running = s.query(ProcessingJob).filter(ProcessingJob.project_id == pid,
                                            ProcessingJob.status == "running").count()
    if running:
        raise ProjectError("Cancel the running job(s) of this project before deleting it")
    s.query(ContentTag).filter(ContentTag.entity_type == "project", ContentTag.entity_id == pid).delete()
    asset_ids = [a.id for a in s.query(GeneratedAsset.id).filter_by(project_id=pid)]
    if asset_ids:
        s.query(ContentTag).filter(ContentTag.entity_type == "asset", ContentTag.entity_id.in_(asset_ids)).delete(
            synchronize_session=False)
    vector_ids = [r.vector_id for r in s.query(MemoryReference).filter_by(project_id=pid)]
    s.delete(p)
    ctx.audit(s, "project", pid, "delete", workspace=str(ws.name))
    s.flush()
    return {"workspace": ws, "vector_ids": vector_ids}


def finish_delete(ctx, info: dict) -> bool:
    """Called after the DB commit: remove vectors and files. Never raises."""
    ok = True
    try:
        ctx.memory._delete_vectors(info["vector_ids"])
    except Exception:
        ok = False
    ws = info["workspace"]
    try:
        ensure_within(ws, [ctx.settings.projects_dir])
        left = remove_tree(ws)
        if left:
            ok = False
            log.warning("Project folder %s was not fully removed; %d item(s) left (e.g. %s). The orphan-folder cleanup "
                        "will remove it later.", ws, len(left), left[0])
    except Exception as e:
        ok = False
        log.warning("Could not remove project folder %s: %s", ws, e)
    return ok


# ------------------------------- orphan workspace folders ---------------------
def orphan_workspaces(ctx, s) -> list:
    """Folders in the projects directory that no project row points to (left behind by an earlier delete that could
    not finish, or by hand). Each: name, path, files, bytes, age_seconds, empty (no files at all)."""
    root = Path(ctx.settings.projects_dir)
    if not root.exists():
        return []
    known = {os.path.normcase(os.path.realpath(p.workspace_dir)) for p in s.query(Project).all() if p.workspace_dir}
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or os.path.normcase(os.path.realpath(d)) in known:
            continue
        files = [f for f in d.rglob("*") if f.is_file()]
        out.append({"name": d.name, "path": str(d), "files": len(files), "bytes": sum(f.stat().st_size for f in files),
                    "empty": not files, "age_seconds": int(time.time() - d.stat().st_mtime)})
    return out


def cleanup_orphan_workspaces(ctx, s, *, dry_run: bool = True, include_files: bool = False, min_age_s: int = 600) -> dict:
    """Remove orphan project folders. By default only EMPTY shells (no files at all) older than ``min_age_s`` are
    removed; a folder that still holds files is kept unless ``include_files`` is set. ``dry_run`` only reports."""
    removed, kept = [], []
    for o in orphan_workspaces(ctx, s):
        why = ("it still holds files (pass include_files to remove it)" if o["files"] and not include_files else
               "it was changed in the last few minutes" if o["age_seconds"] < min_age_s else None)
        if why:
            kept.append({**o, "reason": why})
            continue
        if not dry_run:
            ensure_within(Path(o["path"]), [ctx.settings.projects_dir])
            left = remove_tree(Path(o["path"]))
            if left:
                kept.append({**o, "reason": f"could not be removed ({len(left)} item(s) left)"})
                continue
        removed.append(o)
    return {"dry_run": dry_run, "removed": removed, "kept": kept}


# ------------------------------- videos ------------------------------------
def save_upload(ctx, project: Project, stream, original_name: str) -> Path:
    """Stream an upload to disk with extension + size validation. ``stream`` has .read(n)."""
    name = sanitize_filename(original_name, "video.mp4")
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXTENSIONS:
        raise ProjectError(f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(VIDEO_EXTENSIONS))}")
    tmp = ctx.settings.tmp_dir / f"upload_{uuid.uuid4().hex}{ext}"
    total, limit = 0, ctx.settings.max_upload_bytes
    try:
        with open(tmp, "wb") as f:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ProjectError(f"File exceeds the {limit // 1024**3} GB upload limit")
                f.write(chunk)
        if total == 0:
            raise ProjectError("The uploaded file is empty")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def attach_video(ctx, s, project: Project, tmp_file: Path, original_name: str, replace: bool = False) -> SourceVideo:
    """Validate (ffprobe), move into the workspace (never overwriting), thumbnail, record."""
    name = sanitize_filename(original_name, "video.mp4")
    try:
        info = media.probe(tmp_file)
    except media.MediaError as e:
        tmp_file.unlink(missing_ok=True)
        raise ProjectError(f"Not a valid video: {e}")
    digest = media.sha256_file(tmp_file)
    dest_dir = workspace_of(project) / "source"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    n = 1
    while dest.exists():                       # never overwrite an existing original
        dest = dest_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    shutil.move(str(tmp_file), dest)
    if replace:
        for old in s.query(SourceVideo).filter_by(project_id=project.id, is_current=True):
            old.is_current = False
    v = SourceVideo(project_id=project.id, original_name=original_name[:300], path=str(dest), sha256=digest,
                    size_bytes=info["size_bytes"] or dest.stat().st_size, duration=info["duration"],
                    width=info["width"], height=info["height"], fps=info["fps"],
                    video_codec=info["video_codec"], audio_tracks=info["audio_tracks"],
                    audio_codec=info["audio_codec"])
    s.add(v)
    s.flush()
    try:
        th = media.make_thumbnail(dest, workspace_of(project) / "thumbs" / f"video_{v.id}.jpg")
        v.thumbnail_path = str(th)
        project.thumbnail_path = str(th)
    except media.MediaError:
        pass
    if project.status in ("draft", "uploaded", "failed"):
        project.status = "uploaded"
    ctx.audit(s, "project", project.id, "video_replaced" if replace else "video_uploaded", video=v.id,
              sha256=digest[:12])
    return v


def duplicates_of(s, video: SourceVideo) -> list:
    rows = s.query(SourceVideo, Project.name).join(Project, Project.id == SourceVideo.project_id) \
        .filter(SourceVideo.sha256 == video.sha256, SourceVideo.id != video.id).all()
    return [{"project_id": v.project_id, "project_name": n, "video_id": v.id} for v, n in rows]


def delete_video(ctx, s, v: SourceVideo, project: Project):
    path = Path(v.path)
    ensure_within(path, [ctx.settings.projects_dir])
    thumb = Path(v.thumbnail_path) if v.thumbnail_path else None
    s.delete(v)
    ctx.audit(s, "project", project.id, "video_deleted", video=v.id)
    s.flush()
    path.unlink(missing_ok=True)
    if thumb:
        thumb.unlink(missing_ok=True)
    remaining = s.query(SourceVideo).filter_by(project_id=project.id, is_current=True).count()
    if not remaining:
        project.thumbnail_path = None
        if project.status == "uploaded":
            project.status = "draft"


def current_video(s, project_id: int) -> SourceVideo | None:
    return s.query(SourceVideo).filter_by(project_id=project_id, is_current=True).order_by(SourceVideo.id.desc()).first()


def serialize(s, p: Project) -> dict:
    v = current_video(s, p.id)
    counts = {}
    for kind, in s.query(GeneratedAsset.kind).filter_by(project_id=p.id):
        counts[kind] = counts.get(kind, 0) + 1
    return {"id": p.id, "name": p.name, "description": p.description, "status": p.status,
            "content_type": p.content_type, "tags": get_tags(s, "project", p.id),
            "has_thumbnail": bool(p.thumbnail_path and Path(p.thumbnail_path).exists()),
            "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat(),
            "video": video_dict(v) if v else None, "asset_counts": counts,
            "has_transcript": s.query(Transcript.id).filter_by(project_id=p.id).first() is not None}


def video_dict(v: SourceVideo) -> dict:
    return {"id": v.id, "original_name": v.original_name, "duration": v.duration, "width": v.width,
            "height": v.height, "fps": v.fps, "size_bytes": v.size_bytes, "video_codec": v.video_codec,
            "audio_tracks": v.audio_tracks, "audio_codec": v.audio_codec, "sha256": v.sha256,
            "has_thumbnail": bool(v.thumbnail_path and Path(v.thumbnail_path).exists()),
            "created_at": v.created_at.isoformat(), "is_current": v.is_current}
