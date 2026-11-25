"""Creative Studio API: status, brief, concept generation, recompose/regenerate, hardware benchmark."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ruha.api.deps import app_ctx, get_or_404
from ruha.creative import brief as brief_mod
from ruha.creative import formats, studio, styles
from ruha.models import GeneratedAsset, Project, Transcript
from ruha.services import assets as asset_svc

router = APIRouter(prefix="/api", tags=["creative"])


class BriefIn(BaseModel):
    brief: dict


class GenerateIn(BaseModel):
    formats: list = ["poster"]
    styles: list | None = None
    headlines: list | None = None     # e.g. the sermon's encouragement hooks: one per concept, used verbatim


class EditIn(BaseModel):
    headline: str | None = None
    supporting_text: str | None = None
    scripture_reference: str | None = None


@router.get("/creative/status")
def status(ctx=Depends(app_ctx)):
    cfg = ctx.config.all()
    st = ctx.services.images.status(cfg)
    return {**st, "styles": styles.public_styles(),
            "formats": [{"id": k, "label": v["label"], "size": list(v["size"]), "max_words": v["max_words"],
                         "note": v.get("note", "")} for k, v in formats.FORMATS.items()],
            "default_styles": styles.DEFAULT_TRIO, "benchmark": cfg.get("creative.benchmark") or {}}


@router.post("/creative/benchmark", status_code=202)
def benchmark(ctx=Depends(app_ctx)):
    return {"job_id": ctx.queue.enqueue("creative_benchmark", None, {})}


@router.get("/projects/{pid}/creative")
def workspace(pid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        ws = p.workspace_dir
        t = s.query(Transcript).filter_by(project_id=pid).order_by(Transcript.id.desc()).first()
        source = None
        if t:
            plan = t.plan or {}
            source = {"title": plan.get("title") or p.name, "main_theme": plan.get("main_theme", ""),
                      "summary": t.summary or plan.get("summary", ""), "key_points": plan.get("key_points", [])[:8],
                      "visual_theme": plan.get("visual_theme", ""),
                      "encouragement": [x.get("headline") or x.get("hook", "") for x in plan.get("encouragement_posts", []) if (x.get("headline") or x.get("hook"))]}
        items = [asset_svc.serialize(a, s) for a in
                 s.query(GeneratedAsset).filter_by(project_id=pid, kind="creative").order_by(GeneratedAsset.id.desc())]
    from pathlib import Path
    return {"project": {"id": pid, "name": p.name}, "source": source, "brief": studio.load_brief(Path(ws)),
            "assets": items}


@router.post("/projects/{pid}/creative/brief", status_code=202)
def make_brief(pid: int, ctx=Depends(app_ctx)):
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Project")
        if not s.query(Transcript).filter_by(project_id=pid).first():
            raise HTTPException(400, "Run the transcription first - the brief is written from the transcript")
    return {"job_id": ctx.queue.enqueue("creative_brief", pid, {})}


@router.put("/projects/{pid}/creative/brief")
def save_brief(pid: int, body: BriefIn, ctx=Depends(app_ctx)):
    from pathlib import Path
    with ctx.db.session() as s:
        p = get_or_404(s, Project, pid, "Project")
        ws = Path(p.workspace_dir)
    return studio.save_brief(ws, brief_mod.normalise_edited_brief(body.brief))


@router.post("/projects/{pid}/creative/generate", status_code=202)
def generate(pid: int, body: GenerateIn, ctx=Depends(app_ctx)):
    bad = [f for f in body.formats if f not in formats.FORMATS]
    if bad or not body.formats:
        raise HTTPException(400, f"formats must be from {list(formats.FORMATS)}")
    sel = body.styles or styles.DEFAULT_TRIO
    unknown = [x for x in sel if x not in styles.STYLES]
    if unknown:
        raise HTTPException(400, f"Unknown style(s): {unknown}")
    with ctx.db.session() as s:
        get_or_404(s, Project, pid, "Project")
        if not s.query(Transcript).filter_by(project_id=pid).first():
            raise HTTPException(400, "Run the transcription first - creatives are designed from the transcript")
    if studio._need_images(sel):                              # fail fast with the honest reason, not after queuing
        st = ctx.services.images.status(ctx.config.all())
        if not st["available"]:
            raise HTTPException(409, st["reason"] + " Only the 'Minimalist typography' design works without an image model.")
    params = {"formats": body.formats, "styles": sel}
    if body.headlines:
        params["headlines"] = [str(h).strip() for h in body.headlines if str(h).strip()][:8]
    return {"job_id": ctx.queue.enqueue("creative_generation", pid, params)}


def _creative_or_404(ctx, aid):
    with ctx.db.session() as s:
        a = get_or_404(s, GeneratedAsset, aid, "Creative")
        if a.kind != "creative":
            raise HTTPException(400, "Not a creative")
        return a.project_id


@router.post("/creative/{aid}/recompose", status_code=202)
def recompose(aid: int, body: EditIn, ctx=Depends(app_ctx)):
    pid = _creative_or_404(ctx, aid)
    edits = {k: v for k, v in body.model_dump().items() if v is not None}
    if not edits:
        raise HTTPException(400, "Nothing to change")
    return {"job_id": ctx.queue.enqueue("creative_generation", pid, {"mode": "recompose", "asset_id": aid, "edits": edits})}


@router.post("/creative/{aid}/regenerate", status_code=202)
def regenerate(aid: int, ctx=Depends(app_ctx)):
    pid = _creative_or_404(ctx, aid)
    return {"job_id": ctx.queue.enqueue("creative_generation", pid, {"mode": "regenerate", "asset_id": aid})}
