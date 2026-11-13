from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ruha.api.deps import app_ctx, get_or_404
from ruha.models import JobLog, ProcessingJob, Project

router = APIRouter(prefix="/api", tags=["jobs"])


def job_dict(j, names=None) -> dict:
    return {"id": j.id, "uid": j.uid, "project_id": j.project_id,
            "project_name": (names or {}).get(j.project_id), "job_type": j.job_type, "status": j.status,
            "progress": j.progress, "step": j.step, "error": j.error, "attempts": j.attempts,
            "depends_on_id": j.depends_on_id, "params": j.params, "result": j.result,
            "created_at": j.created_at.isoformat(),
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None}


@router.get("/jobs")
def list_jobs(status: str = "", job_type: str = "", project_id: int | None = None, limit: int = 100,
              offset: int = 0, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        qs = s.query(ProcessingJob)
        if status:
            qs = qs.filter(ProcessingJob.status.in_(status.split(",")))
        if job_type:
            qs = qs.filter(ProcessingJob.job_type == job_type)
        if project_id:
            qs = qs.filter(ProcessingJob.project_id == project_id)
        total = qs.count()
        names = {i: n for i, n in s.query(Project.id, Project.name)}
        rows = qs.order_by(ProcessingJob.id.desc()).offset(offset).limit(min(limit, 500)).all()
        return {"total": total, "paused": ctx.queue.paused(), "items": [job_dict(j, names) for j in rows]}


@router.get("/jobs/{jid}")
def get_job(jid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        j = get_or_404(s, ProcessingJob, jid, "Job")
        d = job_dict(j, {j.project_id: s.query(Project.name).filter_by(id=j.project_id).scalar()})
        d["logs"] = [{"id": g.id, "level": g.level, "message": g.message, "at": g.created_at.isoformat()}
                     for g in s.query(JobLog).filter_by(job_id=jid).order_by(JobLog.id)]
        return d


@router.post("/jobs/{jid}/cancel")
def cancel(jid: int, ctx=Depends(app_ctx)):
    if not ctx.queue.cancel(jid):
        raise HTTPException(409, "Only queued or running jobs can be cancelled")
    return {"cancelled": jid}


@router.post("/jobs/{jid}/retry")
def retry(jid: int, ctx=Depends(app_ctx)):
    if not ctx.queue.retry(jid):
        raise HTTPException(409, "Only failed or cancelled jobs can be retried")
    return {"retried": jid}


@router.post("/queue/retry-failed")
def retry_failed(project_id: int | None = None, ctx=Depends(app_ctx)):
    return {"retried": ctx.queue.retry_failed(project_id)}


@router.post("/queue/pause")
def pause(ctx=Depends(app_ctx)):
    ctx.queue.set_paused(True)
    return {"paused": True}


@router.post("/queue/resume")
def resume(ctx=Depends(app_ctx)):
    ctx.queue.set_paused(False)
    return {"paused": False}
