import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from .api import ReportCache, router
from .assistant_api import router as assistant_router
from .config import get_settings
from .db import make_database
from .models import now_ms
from .platforms import Platforms
from .worker import Worker


def create_app(settings=None):
    settings = settings or get_settings()
    engine, sessions = make_database(settings)

    @asynccontextmanager
    async def lifespan(app):
        settings.validate_runtime()
        async with engine.connect() as db:
            await db.execute(text("PRAGMA journal_mode=WAL"))
            # Fail clearly when migrations were not run. Startup never silently creates tables.
            await db.execute(text("SELECT id FROM tenants LIMIT 1"))
        platforms = Platforms(settings)
        worker = Worker(settings, sessions, platforms)
        app.state.platforms, app.state.worker = platforms, worker
        task = asyncio.create_task(worker.run()) if settings.worker_enabled else None
        yield
        worker.stopping.set()
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await platforms.close()
        await engine.dispose()

    app = FastAPI(
        title="企微用户流失监控",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.app_env == "production" else "/openapi.json",
    )
    app.state.settings, app.state.sessions, app.state.engine = settings, sessions, engine
    app.state.login_attempts = {}
    app.state.assistant_attempts, app.state.assistant_active = {}, set()
    app.state.report_cache = ReportCache(settings.report_cache_seconds * 1000)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.request_id = secrets.token_hex(8)
        origin = request.headers.get("origin")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin
            and origin != settings.public_origin
        ):
            return JSONResponse(
                {"error": {"code": "403", "message": "请求来源不受信任"}}, status_code=403
            )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Media rows are content-addressed and immutable; they carry their own
        # private long cache policy instead of the blanket API no-store.
        if request.url.path.startswith("/api/") and not request.url.path.startswith(
            "/api/v1/media/"
        ):
            response.headers["Cache-Control"] = "no-store"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            {
                "error": {"code": str(exc.status_code), "message": exc.detail},
                "request_id": getattr(request.state, "request_id", ""),
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not reflect input values such as login codes or passwords.
        return JSONResponse(
            {
                "error": {"code": "422", "message": "请求参数格式不正确"},
                "request_id": getattr(request.state, "request_id", ""),
            },
            status_code=422,
        )

    @app.exception_handler(OperationalError)
    async def db_error(request, exc):
        logging.getLogger(__name__).error(
            "database_unavailable request=%s", request.state.request_id
        )
        return JSONResponse(
            {"error": {"code": "503", "message": "数据库暂不可用，请重试"}}, status_code=503
        )

    app.include_router(router)
    app.include_router(assistant_router)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready():
        async with sessions() as db:
            await db.execute(text("SELECT 1"))
        if settings.worker_enabled and now_ms() - app.state.worker.last_tick > 180_000:
            raise HTTPException(503, "worker unavailable")
        return {"status": "ready"}

    dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    async def index():
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse({"message": "后端已运行，请先构建 frontend；开发接口文档位于 /docs"})

    return app


app = create_app()
