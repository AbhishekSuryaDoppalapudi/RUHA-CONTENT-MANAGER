"""Dashboard metrics and analytics - every number is computed from database records."""
from __future__ import annotations

import csv
import io
import shutil
import time
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func

from ruha.models import (Approval, GeneratedAsset, ProcessingJob, Project, SourceVideo, Transcript, Translation,
                         utcnow)

_size_cache = {"t": 0.0, "v": 0}


def storage_used(app) -> int:
    if time.monotonic() - _size_cache["t"] < 20:
        return _size_cache["v"]
    total = 0
    for p in Path(app.settings.data_dir).rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    _size_cache.update(t=time.monotonic(), v=total)
    return total


def dashboard(app) -> dict:
    from ruha.services import assets as asset_svc
    with app.db.session() as s:
        kinds = dict(s.query(GeneratedAsset.kind, func.count()).group_by(GeneratedAsset.kind).all())
        jobs = dict(s.query(ProcessingJob.status, func.count()).group_by(ProcessingJob.status).all())
        vids_done = s.query(func.count(func.distinct(Transcript.video_id))).scalar() or 0
        metrics = {
            "total_projects": s.query(func.count(Project.id)).filter(Project.status != "archived").scalar(),
            "archived_projects": s.query(func.count(Project.id)).filter(Project.status == "archived").scalar(),
            "videos_uploaded": s.query(func.count(SourceVideo.id)).scalar(),
            "videos_processed": vids_done,
            "reels_generated": kinds.get("main_reel", 0) + kinds.get("short_reel", 0) + kinds.get("image_reel", 0),
            "posters_created": kinds.get("poster", 0),
            "social_posts_created": kinds.get("social_post", 0),
            "translations_completed": s.query(func.count(Translation.id)).filter_by(status="completed").scalar(),
            "processing_jobs": sum(jobs.values()),
            "failed_jobs": jobs.get("failed", 0),
        }
        du = shutil.disk_usage(app.settings.data_dir)
        metrics["storage_used_bytes"] = storage_used(app)
        metrics["storage_available_bytes"] = du.free
        metrics["disk_total_bytes"] = du.total
        processing = {"active": jobs.get("running", 0), "queued": jobs.get("queued", 0),
                      "completed": jobs.get("completed", 0), "failed": jobs.get("failed", 0),
                      "running_jobs": [{"id": j.id, "type": j.job_type, "project_id": j.project_id,
                                        "progress": j.progress, "step": j.step}
                                       for j in s.query(ProcessingJob).filter_by(status="running")],
                      "paused": app.queue.paused()}
        names = {i: n for i, n in s.query(Project.id, Project.name)}
        recent = {
            "uploads": [{"id": v.id, "project_id": v.project_id, "project": names.get(v.project_id),
                         "name": v.original_name, "at": v.created_at.isoformat()}
                        for v in s.query(SourceVideo).order_by(SourceVideo.id.desc()).limit(6)],
            "completed_projects": [{"id": p.id, "name": p.name, "status": p.status, "at": p.updated_at.isoformat()}
                                   for p in s.query(Project).filter(Project.status.in_(["review_required", "approved", "published"]))
                                   .order_by(Project.updated_at.desc()).limit(6)],
            "generated": [{"id": a.id, "kind": a.kind, "title": a.title, "project_id": a.project_id,
                           "project": names.get(a.project_id), "at": a.created_at.isoformat()}
                          for a in s.query(GeneratedAsset).filter(GeneratedAsset.kind.in_(
                              ["main_reel", "short_reel", "image_reel", "poster", "social_post", "creative"])).order_by(GeneratedAsset.id.desc()).limit(8)],
            "approvals": [{"asset_id": ap.asset_id, "action": ap.action, "reason": ap.reason, "at": ap.created_at.isoformat()}
                          for ap in s.query(Approval).filter(Approval.action != "comment").order_by(Approval.id.desc()).limit(6)],
            "failed_jobs": [{"id": j.id, "type": j.job_type, "project_id": j.project_id, "error": j.error,
                             "at": (j.completed_at or j.created_at).isoformat()}
                            for j in s.query(ProcessingJob).filter_by(status="failed").order_by(ProcessingJob.id.desc()).limit(6)],
        }
        review = dict(s.query(GeneratedAsset.status, func.count()).filter(GeneratedAsset.kind.in_(
            ["main_reel", "short_reel", "image_reel", "poster", "social_post", "creative"])).group_by(GeneratedAsset.status).all())
    return {"metrics": metrics, "processing": processing, "recent": recent, "review_queue": review}


def analytics(app, days: int = 90) -> dict:
    since = utcnow() - timedelta(days=days)
    with app.db.session() as s:
        review_kinds = ["main_reel", "short_reel", "image_reel", "poster", "social_post", "creative"]
        base = s.query(GeneratedAsset).filter(GeneratedAsset.kind.in_(review_kinds))
        status = Counter(a.status for a in base)
        total = sum(status.values())
        revisions = s.query(func.count(Approval.id)).filter(Approval.action == "revision_requested").scalar() or 0
        versions = sum(max(0, a.version - 1) for a in base)
        cat = Counter(a.kind for a in base)
        jobs = s.query(ProcessingJob).filter(ProcessingJob.created_at >= since).all()
        failures = Counter(j.job_type for j in jobs if j.status == "failed")
        prod = defaultdict(lambda: {"count": 0, "seconds": 0.0})
        for j in jobs:
            if j.status == "completed" and j.started_at and j.completed_at:
                prod[j.job_type]["count"] += 1
                prod[j.job_type]["seconds"] += (j.completed_at - j.started_at).total_seconds()
        topics = Counter()
        for t in s.query(Transcript):
            for th in t.themes:
                topics[str(th).strip().lower()] += 1
        daily = defaultdict(int)
        for size, at in s.query(GeneratedAsset.size_bytes, GeneratedAsset.created_at):
            daily[at.date().isoformat()] += size or 0
        for size, at in s.query(SourceVideo.size_bytes, SourceVideo.created_at):
            daily[at.date().isoformat()] += size or 0
        cum, growth = 0, []
        for d in sorted(daily):
            cum += daily[d]
            growth.append({"date": d, "added_bytes": daily[d], "cumulative_bytes": cum})
        out = {
            "window_days": days,
            "assets": {"total": total, "approved": status.get("approved", 0), "rejected": status.get("rejected", 0),
                       "draft": status.get("draft", 0), "revision_requested": status.get("revision_requested", 0)},
            "revisions": {"revision_requests": revisions, "extra_versions": versions},
            "videos_processed": s.query(func.count(func.distinct(Transcript.video_id))).scalar() or 0,
            "production_time": {k: {"count": v["count"], "total_seconds": round(v["seconds"], 1),
                                    "avg_seconds": round(v["seconds"] / v["count"], 1) if v["count"] else 0}
                                for k, v in prod.items()},
            "topics": [{"topic": k, "count": v} for k, v in topics.most_common(15)],
            "category_distribution": dict(cat),
            "processing_failures": dict(failures),
            "storage_growth": growth,
            "external_metrics": {"available": False,
                                 "note": "No social-platform integration is authorised, so no engagement data is shown."},
        }
    return out


def analytics_csv(data: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "key", "value"])
    for sec in ("assets", "revisions", "category_distribution", "processing_failures"):
        for k, v in data[sec].items():
            w.writerow([sec, k, v])
    for k, v in data["production_time"].items():
        w.writerow(["production_time", k, f"count={v['count']};total_s={v['total_seconds']};avg_s={v['avg_seconds']}"])
    for t in data["topics"]:
        w.writerow(["topics", t["topic"], t["count"]])
    for g in data["storage_growth"]:
        w.writerow(["storage_growth", g["date"], g["cumulative_bytes"]])
    w.writerow(["videos_processed", "", data["videos_processed"]])
    return buf.getvalue()
