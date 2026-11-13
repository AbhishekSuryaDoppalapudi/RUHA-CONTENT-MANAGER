from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ruha.api.deps import app_ctx
from ruha.models import Project

router = APIRouter(prefix="/api/memory", tags=["memory"])


class PrefIn(BaseModel):
    text: str
    category: str = "general"


class DeleteIn(BaseModel):
    ids: list = []
    kinds: list = []
    project_id: int | None = None
    everything: bool = False


class SimilarIn(BaseModel):
    text: str
    kinds: list = ["post", "caption"]
    threshold: float = 0.8


@router.get("/stats")
def stats(ctx=Depends(app_ctx)):
    return ctx.memory.stats()


@router.get("/search")
def search(q: str, kinds: str = "", n: int = 10, project_id: int | None = None, ctx=Depends(app_ctx)):
    hits = ctx.memory.search(q, kinds=[k for k in kinds.split(",") if k] or None, n=min(n, 50), project_id=project_id)
    with ctx.db.session() as s:
        names = {i: nm for i, nm in s.query(Project.id, Project.name)}
    for h in hits:
        h["project_name"] = names.get(h["project_id"])
    return {"query": q, "embedder": ctx.memory.embedder().name, "hits": hits}


@router.get("/items")
def items(kind: str = "", q: str = "", project_id: int | None = None, limit: int = 50, offset: int = 0,
          ctx=Depends(app_ctx)):
    return ctx.memory.list_refs(kind or None, q or None, project_id, min(limit, 500), offset)


@router.post("/preferences", status_code=201)
def add_pref(body: PrefIn, ctx=Depends(app_ctx)):
    if not body.text.strip():
        raise HTTPException(400, "Preference text is required")
    ctx.memory.remember_preference(body.text.strip(), body.category)
    return {"ok": True}


@router.get("/topics")
def topics(ctx=Depends(app_ctx)):
    return {"covered": ctx.memory.covered_topics(), "recommendations": ctx.memory.recommend_topics()}


@router.post("/similar")
def similar(body: SimilarIn, ctx=Depends(app_ctx)):
    return {"matches": ctx.memory.similar(body.text, body.kinds, body.threshold)}


@router.get("/export")
def export(ctx=Depends(app_ctx)):
    return JSONResponse(ctx.memory.export(), headers={"Content-Disposition": 'attachment; filename="ruha-memory.json"'})


@router.post("/delete")
def delete(body: DeleteIn, ctx=Depends(app_ctx)):
    if not (body.ids or body.kinds or body.project_id or body.everything):
        raise HTTPException(400, "Specify ids, kinds, project_id or everything=true")
    return {"deleted": ctx.memory.delete(body.ids or None, body.kinds or None, body.project_id, None, body.everything)}


@router.post("/reindex")
def reindex(ctx=Depends(app_ctx)):
    return {"indexed": ctx.memory.reindex()}
