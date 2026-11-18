"""FastAPI application factory + launcher.  Run:  python -m ruha.main"""
from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from ruha import __version__
from ruha.config import ROOT, Settings, load_settings
from ruha.context import AppContext, Services
from ruha.security import PathSecurityError, redact

log = logging.getLogger("ruha")


class RedactFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(str(record.msg))
        return True


def setup_logging(settings: Settings, level: str = "INFO"):
    root = logging.getLogger("ruha")
    root.setLevel(getattr(logging, level, logging.INFO))
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) and
               Path(h.baseFilename) == settings.logs_dir / "ruha.log" for h in root.handlers):
        h = logging.handlers.RotatingFileHandler(settings.logs_dir / "ruha.log", maxBytes=2_000_000, backupCount=3,
                                                 encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h.addFilter(RedactFilter())
        root.addHandler(h)


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or load_settings()
    ctx = AppContext(settings, services)
    from ruha.services.brand import ensure_default_templates
    ensure_default_templates(ctx)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(settings, ctx.config.get("logging.level", "INFO"))
        ctx.start()
        log.info("El Ruach Manager %s started (data: %s)", __version__, settings.data_dir.name)
        yield
        ctx.stop()

    app = FastAPI(title="El Ruach Manager API", version=__version__, lifespan=lifespan,
                  description="Local-first content management for El Ruach AI Video & Content Studio")
    app.state.ctx = ctx
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"],
                       allow_headers=["*"])

    from ruha.api import assets, creative, jobs, memory, projects, studio, system, yt
    for mod in (projects, assets, jobs, memory, studio, system, creative, yt):
        app.include_router(mod.router)

    @app.exception_handler(PathSecurityError)
    async def _path(_: Request, e):
        return JSONResponse({"detail": "Access to that file is not allowed"}, status_code=403)

    from ruha.services.projects import ProjectError
    from ruha.services.assets import AssetError
    from ruha.services.planner import PlanError

    @app.exception_handler(ProjectError)
    @app.exception_handler(AssetError)
    @app.exception_handler(PlanError)
    async def _bad(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(LookupError)
    async def _nf(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(Exception)
    async def _err(_: Request, e):
        log.exception("unhandled error")
        return JSONResponse({"detail": "Internal error - details were written to the log"}, status_code=500)

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = (dist / path).resolve()
            if path and dist.resolve() in f.parents and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    return app


def main():
    import uvicorn
    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
