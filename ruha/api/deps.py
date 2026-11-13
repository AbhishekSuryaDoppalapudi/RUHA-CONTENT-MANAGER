from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from ruha.security import ensure_within


def app_ctx(request: Request):
    return request.app.state.ctx


def get_or_404(s, model, ident, label: str | None = None):
    obj = s.get(model, ident)
    if obj is None:
        raise HTTPException(404, f"{label or model.__name__} not found")
    return obj


def file_response(ctx, path, *, filename: str | None = None, download: bool = False, media_type=None):
    """Serve a file only if it exists inside the configured storage directories."""
    if not path:
        raise HTTPException(404, "No file")
    p = ensure_within(path, ctx.settings.allowed_roots)
    if not p.is_file():
        raise HTTPException(404, "File is missing on disk")
    kw = {"media_type": media_type} if media_type else {}
    if download:
        return FileResponse(p, filename=filename or p.name, **kw)
    return FileResponse(p, **kw)


def page(request_limit: int, request_offset: int, max_limit: int = 500):
    return max(1, min(request_limit, max_limit)), max(0, request_offset)
