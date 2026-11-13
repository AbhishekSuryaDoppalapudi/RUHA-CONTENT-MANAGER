"""System status, settings, backup/restore, analytics and dashboard endpoints."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text

from ruha import __version__
from ruha.api.deps import app_ctx, file_response
from ruha.config import DEFAULT_CONFIG, QUALITY_CRF, ROOT
from ruha.security import temp_file
from ruha.models import (GeneratedAsset, ProcessingJob, Project, SourceVideo)
from ruha.services import backup as backup_svc
from ruha.services import insights
from ruha.services import system as sysinfo

router = APIRouter(prefix="/api", tags=["system"])

VALID = {
    "whisper.model": lambda v: v in sysinfo.WHISPER_MODELS,
    "whisper.device": lambda v: v in ("auto", "cuda", "cpu"),
    "gpu.mode": lambda v: v in ("auto", "gpu", "cpu"),
    "render.quality": lambda v: v in QUALITY_CRF,
    "render.width": lambda v: isinstance(v, int) and 240 <= v <= 4320,
    "render.height": lambda v: isinstance(v, int) and 240 <= v <= 4320,
    "embedding.provider": lambda v: v in ("auto", "ollama", "hash"),
    "app.theme": lambda v: v in ("dark", "light"),
    "llm.temperature": lambda v: isinstance(v, (int, float)) and 0 <= v <= 2,
    "logging.level": lambda v: v in ("DEBUG", "INFO", "WARNING", "ERROR"),
    "llm.base_url": lambda v: isinstance(v, str) and v.startswith(("http://localhost", "http://127.0.0.1")),
    "gpu.min_free_ram_gb": lambda v: isinstance(v, (int, float)) and 0 <= v <= 64,
}


class SettingsIn(BaseModel):
    values: dict


@router.get("/health")
def health(ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        s.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__, "schema_version": ctx.db.schema_version()}


@router.get("/dashboard")
def dashboard(ctx=Depends(app_ctx)):
    return insights.dashboard(ctx)


@router.get("/analytics")
def analytics(days: int = 90, ctx=Depends(app_ctx)):
    return insights.analytics(ctx, max(1, min(days, 3650)))


@router.get("/analytics/export")
def analytics_export(format: str = "json", days: int = 90, ctx=Depends(app_ctx)):
    data = insights.analytics(ctx, days)
    if format == "csv":
        return PlainTextResponse(insights.analytics_csv(data), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="ruha-analytics.csv"'})
    if format != "json":
        raise HTTPException(400, "format must be json or csv")
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="ruha-analytics.json"'})


@router.get("/system/status")
def status(ctx=Depends(app_ctx)):
    return sysinfo.system_status(ctx)


@router.get("/system/info")
def info(ctx=Depends(app_ctx)):
    s = ctx.settings
    with ctx.db.session() as ses:
        counts = {"projects": ses.query(Project).count(), "videos": ses.query(SourceVideo).count(),
                  "assets": ses.query(GeneratedAsset).count(), "jobs": ses.query(ProcessingJob).count()}
    return {"version": __version__, "schema_version": ctx.db.schema_version(),
            "database": {"path": str(s.db_path), "size_bytes": s.db_path.stat().st_size if s.db_path.exists() else 0, **counts},
            "storage": {"data_dir": str(s.data_dir), "projects_dir": str(s.projects_dir), "brand_dir": str(s.brand_dir),
                        "backups_dir": str(s.backups_dir), "chroma_dir": str(s.chroma_dir), "logs_dir": str(s.logs_dir)},
            "limits": {"max_upload_gb": round(s.max_upload_bytes / 1024**3, 1)},
            "output_formats": {"video": "MP4 (H.264/AAC)", "images": "PNG", "subtitles": "SRT"},
            "privacy": {"cloud_ai": False, "external_uploads": False,
                        "network_allowed": bool(ctx.config.get("privacy.allow_network"))}}


@router.get("/settings")
def get_settings(ctx=Depends(app_ctx)):
    return {"values": ctx.config.all(), "by_category": ctx.config.by_category()}


@router.put("/settings")
def put_settings(body: SettingsIn, ctx=Depends(app_ctx)):
    for k, v in body.values.items():
        if k not in DEFAULT_CONFIG:
            raise HTTPException(400, f"Unknown setting '{k}'")
        check = VALID.get(k)
        if check and not check(v):
            raise HTTPException(400, f"Invalid value for '{k}'")
    vals = ctx.config.set_many(body.values)
    if "queue.paused" in body.values:
        ctx.queue._wake.set()
    return {"values": vals}


@router.get("/system/updates")
def updates(ctx=Depends(app_ctx)):
    out = {"version": __version__, "git": None, "check": "disabled", "instructions": [
        "git pull   (in the RUHA-CONTENT-MANAGER folder)", "run update.bat to refresh dependencies and rebuild the UI"]}
    if shutil.which("git") and (ROOT / ".git").exists():
        def g(*a):
            return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, timeout=30).stdout.strip()
        out["git"] = {"branch": g("rev-parse", "--abbrev-ref", "HEAD"), "commit": g("rev-parse", "--short", "HEAD"),
                      "dirty": bool(g("status", "--porcelain"))}
        if ctx.config.get("privacy.allow_network"):
            subprocess.run(["git", "-C", str(ROOT), "fetch", "--quiet"], capture_output=True, timeout=60)
            behind = g("rev-list", "--count", "HEAD..@{u}")
            out["check"] = f"{behind} new commit(s) available" if behind.isdigit() else "no upstream configured"
        else:
            out["check"] = "disabled - enable 'Allow network access' in Privacy settings to check"
    return out


@router.get("/system/logs")
def logs(lines: int = 200, ctx=Depends(app_ctx)):
    p = ctx.settings.logs_dir / "ruha.log"
    if not p.exists():
        return {"lines": []}
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        data = f.readlines()[-max(1, min(lines, 2000)):]
    return {"lines": [x.rstrip("\n") for x in data]}


# ---------------------------------- backup -----------------------------------
class BackupIn(BaseModel):
    include_media: bool = True


@router.get("/backups")
def backups(ctx=Depends(app_ctx)):
    return backup_svc.list_backups(ctx)


@router.post("/backups", status_code=201)
def create_backup(body: BackupIn, ctx=Depends(app_ctx)):
    p = backup_svc.create_backup(ctx, body.include_media)
    return {"name": p.name, "size_bytes": p.stat().st_size}


@router.get("/backups/{name}/download")
def download_backup(name: str, ctx=Depends(app_ctx)):
    try:
        p = backup_svc.backup_path(ctx, name)
    except backup_svc.BackupError as e:
        raise HTTPException(404, str(e))
    return file_response(ctx, p, filename=p.name, download=True)


@router.delete("/backups/{name}")
def delete_backup(name: str, ctx=Depends(app_ctx)):
    try:
        backup_svc.backup_path(ctx, name).unlink()
    except backup_svc.BackupError as e:
        raise HTTPException(404, str(e))
    return {"deleted": name}


@router.post("/backups/{name}/restore")
def restore_backup(name: str, ctx=Depends(app_ctx)):
    try:
        return backup_svc.restore_backup(ctx, backup_svc.backup_path(ctx, name))
    except backup_svc.BackupError as e:
        raise HTTPException(400, str(e))


@router.post("/backups/upload", status_code=201)
def upload_backup(file: UploadFile = File(...), ctx=Depends(app_ctx)):
    tmp = temp_file(ctx.settings.tmp_dir, ".zip")
    with open(tmp, "wb") as f:
        shutil.copyfileobj(file.file, f, 1024 * 1024)
    try:
        backup_svc.inspect_backup(tmp)
    except backup_svc.BackupError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    dest = ctx.settings.backups_dir / f"{backup_svc.BACKUP_PREFIX}uploaded-{tmp.stem[-8:]}.zip"
    tmp.replace(dest)
    return {"name": dest.name}
