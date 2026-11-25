"""YT Video Making API: script -> scenes -> narration -> clips -> finished video."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ruha.api.deps import app_ctx, file_response
from ruha.models import ProcessingJob
from ruha.yt import service

router = APIRouter(prefix="/api/yt", tags=["yt-video-making"])


class PlanIn(BaseModel):
    script: str
    project: str = "mystory"
    style: str | None = None
    max_words: int = 22


class NarrateIn(BaseModel):
    lang: str = "English"


class BuildIn(BaseModel):
    lang: str = "English"
    size: str = "1920x1080"
    burn: bool = False


def _check(lang=None, size=None):
    if lang is not None and lang not in service.LANGUAGES:
        raise HTTPException(400, f"No narration voice for {lang}")
    if size is not None and size not in service.SIZES:
        raise HTTPException(400, f"size must be one of {service.SIZES}")


@router.get("/options")
def options():
    return {"languages": service.LANGUAGES, "sizes": service.SIZES, "default_style": service.sv.DEFAULT_STYLE}


@router.get("/projects")
def projects(ctx=Depends(app_ctx)):
    return {"items": service.list_projects(ctx.settings)}


@router.post("/plan")
def plan(body: PlanIn, ctx=Depends(app_ctx)):
    if not body.script.strip():
        raise HTTPException(400, "Paste your script first")
    sv = service.use(ctx.settings)
    sv.plan(body.script, body.project, max(5, min(body.max_words, 60)), body.style or sv.DEFAULT_STYLE)
    return service.state(ctx.settings, body.project)


@router.get("/projects/{name}")
def get_project(name: str, ctx=Depends(app_ctx)):
    st = service.state(ctx.settings, name)
    jobs = []
    with ctx.db.session() as s:
        for j in s.query(ProcessingJob).filter(ProcessingJob.job_type.in_(["yt_narrate", "yt_video"])) \
                .order_by(ProcessingJob.id.desc()).limit(40):
            try:
                p = json.loads(j.params_json or "{}")
            except ValueError:
                p = {}
            if p.get("name") == st["name"]:
                jobs.append({"id": j.id, "job_type": j.job_type, "status": j.status, "progress": j.progress,
                             "step": j.step, "error": j.error})
    st["jobs"] = jobs[:6]
    return st


def _need_scenes(ctx, name):
    st = service.state(ctx.settings, name)
    if not st["scenes"]:
        raise HTTPException(400, "Plan the scenes first (step 1)")
    return st


@router.post("/projects/{name}/narrate", status_code=202)
def narrate(name: str, body: NarrateIn, ctx=Depends(app_ctx)):
    _check(lang=body.lang)
    st = _need_scenes(ctx, name)
    return {"job_id": ctx.queue.enqueue("yt_narrate", None, {"name": st["name"], "lang": body.lang})}


@router.post("/projects/{name}/build", status_code=202)
def build(name: str, body: BuildIn, ctx=Depends(app_ctx)):
    _check(body.lang, body.size)
    st = _need_scenes(ctx, name)
    if body.lang not in st["narration"]:
        raise HTTPException(400, f"Make the {body.lang} narration first (step 2)")
    return {"job_id": ctx.queue.enqueue("yt_video", None, {"name": st["name"], "lang": body.lang,
                                                           "size": body.size, "burn": body.burn})}


@router.post("/projects/{name}/run-all", status_code=202)
def run_all(name: str, body: BuildIn, ctx=Depends(app_ctx)):
    """Narrate, then build the video, as two chained jobs."""
    _check(body.lang, body.size)
    st = _need_scenes(ctx, name)
    a = ctx.queue.enqueue("yt_narrate", None, {"name": st["name"], "lang": body.lang})
    b = ctx.queue.enqueue("yt_video", None, {"name": st["name"], "lang": body.lang, "size": body.size,
                                             "burn": body.burn}, depends_on=a)
    return {"job_ids": [a, b]}


def _file(ctx, name, lang, ext):
    st = service.state(ctx.settings, name)
    return service.project_dir(ctx.settings, name) / f"{st['name']}_{lang}.{ext}"


@router.get("/projects/{name}/video")
def video(name: str, lang: str = "English", download: bool = False, ctx=Depends(app_ctx)):
    return file_response(ctx, _file(ctx, name, lang, "mp4"), download=download, media_type="video/mp4")


@router.get("/projects/{name}/subtitles")
def subtitles(name: str, lang: str = "English", ctx=Depends(app_ctx)):
    return file_response(ctx, _file(ctx, name, lang, "srt"), download=True)
