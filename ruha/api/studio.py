"""Brand, templates and the content calendar."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func

from ruha.api.deps import app_ctx, file_response, get_or_404
from ruha.config import CALENDAR_STATUSES
from ruha.models import CalendarEntry, GeneratedAsset, Project, Template
from ruha.services import brand as brand_svc

router = APIRouter(prefix="/api", tags=["studio"])


# ---------------------------------- brand ------------------------------------
class BrandIn(BaseModel):
    name: str | None = None
    tagline: str | None = None
    colors: dict | None = None
    export: dict | None = None


class TemplateIn(BaseModel):
    kind: str
    name: str
    config: dict = {}
    is_default: bool = False


TEMPLATE_KINDS = {"poster", "reel", "social", "subtitle", "intro", "outro", "watermark"}


@router.get("/brand")
def get_brand(ctx=Depends(app_ctx)):
    return brand_svc.public_brand(brand_svc.get_brand(ctx))


@router.put("/brand")
def put_brand(body: BrandIn, ctx=Depends(app_ctx)):
    try:
        return brand_svc.update_brand(ctx, body.name, body.tagline, body.colors, body.export)
    except brand_svc.BrandError as e:
        raise HTTPException(400, str(e))


@router.post("/brand/files/{slot}")
def upload_brand_file(slot: str, file: UploadFile = File(...), ctx=Depends(app_ctx)):
    try:
        return brand_svc.save_brand_file(ctx, slot, file.file, file.filename or slot)
    except brand_svc.BrandError as e:
        raise HTTPException(400, str(e))


@router.delete("/brand/files/{slot}")
def delete_brand_file(slot: str, ctx=Depends(app_ctx)):
    try:
        return brand_svc.clear_brand_file(ctx, slot)
    except brand_svc.BrandError as e:
        raise HTTPException(400, str(e))


@router.get("/brand/files/{slot}")
def serve_brand_file(slot: str, ctx=Depends(app_ctx)):
    b = brand_svc.get_brand(ctx)
    if slot not in b["files"]:
        raise HTTPException(404, "Unknown slot")
    return file_response(ctx, b["files"][slot])


# ------------------------- per-project brand kit (logo + text templates) -------------------------
class KitTextIn(BaseModel):
    kind: str
    text: str
    name: str = ""


def _project_ws(ctx, pid: int) -> Path:
    with ctx.db.session() as s:
        return Path(get_or_404(s, Project, pid, "Project").workspace_dir)


def _kit(fn, *a):
    try:
        return fn(*a)
    except brand_svc.BrandError as e:
        raise HTTPException(400, str(e))


@router.get("/projects/{pid}/brand-kit")
def get_kit(pid: int, ctx=Depends(app_ctx)):
    _project_ws(ctx, pid)
    return brand_svc.public_kit(brand_svc.get_kit(ctx, pid))


@router.post("/projects/{pid}/brand-kit/logo")
def upload_kit_logo(pid: int, file: UploadFile = File(...), ctx=Depends(app_ctx)):
    ws = _project_ws(ctx, pid)
    return _kit(brand_svc.save_project_logo, ctx, pid, ws, file.file, file.filename or "logo")


@router.delete("/projects/{pid}/brand-kit/logo")
def delete_kit_logo(pid: int, ctx=Depends(app_ctx)):
    _project_ws(ctx, pid)
    return brand_svc.clear_project_logo(ctx, pid)


@router.get("/projects/{pid}/brand-kit/logo")
def serve_kit_logo(pid: int, ctx=Depends(app_ctx)):
    _project_ws(ctx, pid)
    return file_response(ctx, brand_svc.get_kit(ctx, pid)["logo"])


@router.post("/projects/{pid}/brand-kit/texts", status_code=201)
def add_kit_text(pid: int, body: KitTextIn, ctx=Depends(app_ctx)):
    _project_ws(ctx, pid)
    return _kit(brand_svc.add_text, ctx, pid, body.kind, body.name, body.text)


@router.post("/projects/{pid}/brand-kit/texts/upload")
def upload_kit_texts(pid: int, kind: str = "caption", file: UploadFile = File(...), ctx=Depends(app_ctx)):
    _project_ws(ctx, pid)
    return _kit(brand_svc.import_texts, ctx, pid, kind, file.file, file.filename or "texts.txt")


@router.put("/projects/{pid}/brand-kit/texts/{tid}")
def update_kit_text(pid: int, tid: str, body: KitTextIn, ctx=Depends(app_ctx)):
    _project_ws(ctx, pid)
    return _kit(brand_svc.update_text, ctx, pid, tid, body.kind, body.name, body.text)


@router.delete("/projects/{pid}/brand-kit/texts/{tid}")
def delete_kit_text(pid: int, tid: str, ctx=Depends(app_ctx)):
    _project_ws(ctx, pid)
    return brand_svc.delete_text(ctx, pid, tid)


@router.get("/templates")
def list_templates(kind: str = "", ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        qs = s.query(Template)
        if kind:
            qs = qs.filter_by(kind=kind)
        return [brand_svc.template_dict(t) for t in qs.order_by(Template.kind, Template.id)]


@router.post("/templates", status_code=201)
def create_template(body: TemplateIn, ctx=Depends(app_ctx)):
    if body.kind not in TEMPLATE_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(TEMPLATE_KINDS)}")
    if body.kind in ("poster", "social") and body.config.get("layout", "classic") not in ("classic", "band", "centered"):
        raise HTTPException(400, "layout must be classic, band or centered")
    with ctx.db.session() as s:
        if body.is_default:
            s.query(Template).filter_by(kind=body.kind).update({"is_default": False})
        t = Template(kind=body.kind, name=body.name.strip(), config_json=json.dumps(body.config), is_default=body.is_default)
        s.add(t)
        s.flush()
        return brand_svc.template_dict(t)


@router.put("/templates/{tid}")
def update_template(tid: int, body: TemplateIn, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        t = get_or_404(s, Template, tid, "Template")
        if body.is_default:
            s.query(Template).filter_by(kind=t.kind).update({"is_default": False})
        t.name, t.config_json, t.is_default = body.name.strip(), json.dumps(body.config), body.is_default
        return brand_svc.template_dict(t)


@router.delete("/templates/{tid}")
def delete_template(tid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        t = get_or_404(s, Template, tid, "Template")
        s.delete(t)
    return {"deleted": tid}


# --------------------------------- calendar ----------------------------------
class CalendarIn(BaseModel):
    title: str
    scheduled_for: str
    asset_id: int | None = None
    project_id: int | None = None
    status: str = "draft"
    campaign: str = ""
    category: str = ""
    platform: str = ""
    notes: str = ""
    reminder_at: str | None = None


class CalendarPatch(BaseModel):
    title: str | None = None
    scheduled_for: str | None = None
    asset_id: int | None = None
    status: str | None = None
    campaign: str | None = None
    category: str | None = None
    platform: str | None = None
    notes: str | None = None
    reminder_at: str | None = None
    clear_reminder: bool = False


def _dt(v: str | None):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "")[:19])
    except ValueError:
        raise HTTPException(400, f"Invalid date/time '{v}' (use ISO format)")


def _cal(e: CalendarEntry, s=None) -> dict:
    d = {"id": e.id, "title": e.title, "scheduled_for": e.scheduled_for.isoformat(), "status": e.status,
         "asset_id": e.asset_id, "project_id": e.project_id, "campaign": e.campaign, "category": e.category,
         "platform": e.platform, "notes": e.notes, "reminder_at": e.reminder_at.isoformat() if e.reminder_at else None,
         "reminder_done": e.reminder_done}
    if s is not None and e.asset_id:
        a = s.get(GeneratedAsset, e.asset_id)
        if a:
            d["asset"] = {"id": a.id, "kind": a.kind, "title": a.title, "status": a.status}
    return d


def _check_status(s, status: str, asset_id):
    if status not in CALENDAR_STATUSES:
        raise HTTPException(400, f"status must be one of {CALENDAR_STATUSES}")
    if status != "draft" and asset_id:
        a = s.get(GeneratedAsset, asset_id)
        if a and a.status != "approved":
            raise HTTPException(409, "Only an approved asset can be marked approved/scheduled/published. "
                                     "Approve the asset in Review first.")


@router.get("/calendar")
def calendar(start: str = "", end: str = "", status: str = "", campaign: str = "", category: str = "",
             ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        qs = s.query(CalendarEntry)
        if start:
            qs = qs.filter(CalendarEntry.scheduled_for >= _dt(start))
        if end:
            qs = qs.filter(CalendarEntry.scheduled_for < _dt(end))
        if status:
            qs = qs.filter(CalendarEntry.status == status)
        if campaign:
            qs = qs.filter(CalendarEntry.campaign == campaign)
        if category:
            qs = qs.filter(CalendarEntry.category == category)
        return [_cal(e, s) for e in qs.order_by(CalendarEntry.scheduled_for).limit(2000)]


@router.post("/calendar", status_code=201)
def add_entry(body: CalendarIn, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        if body.asset_id and not s.get(GeneratedAsset, body.asset_id):
            raise HTTPException(404, "Asset not found")
        if body.project_id and not s.get(Project, body.project_id):
            raise HTTPException(404, "Project not found")
        _check_status(s, body.status, body.asset_id)
        pid = body.project_id or (s.get(GeneratedAsset, body.asset_id).project_id if body.asset_id else None)
        e = CalendarEntry(title=body.title.strip() or "Untitled", scheduled_for=_dt(body.scheduled_for), asset_id=body.asset_id,
                          project_id=pid, status=body.status, campaign=body.campaign, category=body.category,
                          platform=body.platform, notes=body.notes, reminder_at=_dt(body.reminder_at))
        s.add(e)
        s.flush()
        ctx.audit(s, "calendar", e.id, "create", title=e.title)
        return _cal(e, s)


@router.patch("/calendar/{eid}")
def patch_entry(eid: int, body: CalendarPatch, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        e = get_or_404(s, CalendarEntry, eid, "Calendar entry")
        new_asset = body.asset_id if body.asset_id is not None else e.asset_id
        if body.status is not None or body.asset_id is not None:
            _check_status(s, body.status or e.status, new_asset)
        for f in ("title", "campaign", "category", "platform", "notes", "status"):
            v = getattr(body, f)
            if v is not None:
                setattr(e, f, v)
        if body.asset_id is not None:
            e.asset_id = body.asset_id
        if body.scheduled_for is not None:
            e.scheduled_for = _dt(body.scheduled_for)
        if body.clear_reminder:
            e.reminder_at, e.reminder_done = None, False
        elif body.reminder_at is not None:
            e.reminder_at, e.reminder_done = _dt(body.reminder_at), False
        return _cal(e, s)


@router.delete("/calendar/{eid}")
def delete_entry(eid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        e = get_or_404(s, CalendarEntry, eid, "Calendar entry")
        s.delete(e)
    return {"deleted": eid}


@router.get("/calendar-meta/campaigns")
def campaigns(ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        camp = [c for c, in s.query(CalendarEntry.campaign).filter(CalendarEntry.campaign != "").distinct()]
        cat = [c for c, in s.query(CalendarEntry.category).filter(CalendarEntry.category != "").distinct()]
    return {"campaigns": sorted(camp), "categories": sorted(cat)}


@router.get("/reminders/due")
def due_reminders(ctx=Depends(app_ctx)):
    now = datetime.now()   # calendar times are naive local times
    with ctx.db.session() as s:
        rows = s.query(CalendarEntry).filter(CalendarEntry.reminder_at != None, CalendarEntry.reminder_done == False,  # noqa: E711,E712
                                             CalendarEntry.reminder_at <= now).order_by(CalendarEntry.reminder_at).all()
        return [_cal(e, s) for e in rows]


@router.post("/reminders/{eid}/done")
def reminder_done(eid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        e = get_or_404(s, CalendarEntry, eid, "Calendar entry")
        e.reminder_done = True
    return {"done": eid}
