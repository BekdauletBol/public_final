"""FastAPI application factory, database start-up, static serving and startup checks."""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

import markdown
from argon2.exceptions import InvalidHashError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from publicsafe import __version__
from publicsafe.api import auth, cameras, health, security, sessions, zones
from publicsafe.api import test as test_api
from publicsafe.api.auth import AdminAuth
from publicsafe.api.errors import STATUS_CODES, ApiError, error_body
from publicsafe.api.ratelimit import SlidingWindowLimiter
from publicsafe.config import DOCS_DIR, ConfigError, Settings, get_settings, startup_checks
from publicsafe.db import migrate
from publicsafe.db.base import make_engine, make_sessionmaker, resolve_database_url

log = logging.getLogger("publicsafe")

#: Content-Security-Policy applied when FastAPI serves the built frontend (Section D).
CSP_POLICY = "default-src 'self'; img-src 'self' blob: data:; connect-src 'self' wss:"

#: Rate-limit rules per route (F-SEC-2): (max requests, window seconds).
RATE_LIMITS: dict[str, list[tuple[int, float]]] = {
    "login": [(5, 60.0)],
    "redeem": [(10, 60.0), (30, 600.0)],
}

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware adding CSP and hardening headers to HTTP responses."""

    def __init__(self, app: ASGIApp, csp: str) -> None:
        self.app = app
        self.csp = csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("content-security-policy", self.csp)
                headers.setdefault("x-content-type-options", "nosniff")
                headers.setdefault("referrer-policy", "same-origin")
                headers.setdefault("x-frame-options", "DENY")
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = STATUS_CODES.get(exc.status_code, "error")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields: dict[str, str] = {}
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            fields[loc or "body"] = str(err.get("msg", "invalid"))
        return JSONResponse(
            status_code=400,
            content=error_body("validation_error", "Request validation failed", {"fields": fields}),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "Internal server error"),
        )


def _render_markdown_page(path: Path, title: str) -> str:
    body = markdown.markdown(path.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>"
        "<style>body{font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:46rem;"
        "margin:2rem auto;padding:0 1rem;color:#1b1b1b;background:#fff}"
        "@media (prefers-color-scheme:dark){body{color:#eee;background:#111}}"
        "code,pre{font-family:ui-monospace,Menlo,monospace}pre{overflow-x:auto}</style>"
        f"</head><body>{body}</body></html>"
    )


def _install_help_pages(app: FastAPI) -> None:
    @app.get("/help/phone", response_class=HTMLResponse, include_in_schema=False)
    def help_phone() -> HTMLResponse:
        path = DOCS_DIR / "PHONE_SETUP.md"
        if not path.is_file():
            raise ApiError(404, "not_found", "PHONE_SETUP.md is missing from docs/")
        return HTMLResponse(_render_markdown_page(path, "public.safe - phone setup"))


def _install_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built frontend with an SPA fallback; never shadow /api or /ws."""
    index = dist / "index.html"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith(("api/", "ws/")) or path in ("api", "ws"):
            raise ApiError(404, "not_found", f"No such route: /{path}")
        candidate = (dist / path).resolve() if path else index
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _init_database(app: FastAPI, settings: Settings) -> None:
    url = resolve_database_url(settings.database_url)
    engine = make_engine(url)
    if settings.auto_migrate:
        migrate.upgrade(url)
    else:
        try:
            migrate.ensure_current(engine, url)
        except migrate.SchemaMismatchError as exc:
            raise ConfigError(str(exc)) from None
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.database_url = url


def db_status(app: FastAPI) -> str:
    try:
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        log.exception("database health check failed")
        return "error"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. ``settings`` is injectable for tests."""
    settings = settings or get_settings()
    # TLS files are checked by uvicorn itself (it needs them); here we check the rest.
    warnings = startup_checks(settings, require_tls=False)
    for message in warnings:
        log.warning(message)

    app = FastAPI(
        title="public.safe",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.started_at = time.monotonic()
    app.state.warnings = warnings

    _init_database(app, settings)
    assert settings.admin_password_hash is not None  # guaranteed by startup_checks
    try:
        app.state.admin_auth = AdminAuth(
            settings.admin_username,
            settings.admin_password_hash,
            dt.timedelta(hours=settings.session_idle_hours),
        )
    except InvalidHashError:
        raise ConfigError(
            "ADMIN_PASSWORD_HASH is not a valid argon2 hash. "
            "Run `publicsafe admin set-password` to create one."
        ) from None
    app.state.limiters = {name: SlidingWindowLimiter(rules) for name, rules in RATE_LIMITS.items()}
    app.state.db_status = lambda: db_status(app)

    _install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(sessions.router)
    app.include_router(cameras.router)
    app.include_router(zones.router)
    app.include_router(security.router)
    if settings.test_mode:
        app.include_router(test_api.router)
    _install_help_pages(app)

    serves_frontend = (settings.frontend_dist / "index.html").is_file()
    if serves_frontend:
        _install_spa(app, settings.frontend_dist)
        app.add_middleware(SecurityHeadersMiddleware, csp=CSP_POLICY)
    else:
        log.info("frontend/dist not found; API only (use the Vite dev server or `make build`)")
    return app
