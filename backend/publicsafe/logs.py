"""Structured logging: one JSON object per line with request and camera ids (Section D, M6).

Stdlib ``logging`` with a JSON formatter, so every library that logs (uvicorn,
SQLAlchemy, ultralytics) ends up in the same stream. ``request_id`` and
``camera_id`` come from context variables: the HTTP middleware sets the
request id (and echoes it as ``X-Request-ID``), the camera socket and the
inference worker set the camera id. ``LOG_JSON=false`` prints the same fields
as plain text for reading in a terminal.

Never pass PINs, tokens, cookies, passwords or frame contents to a logger
(spec O.2); paths are logged without their query string for the same reason.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import re
import secrets
import sys
import time
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
camera_id_var: ContextVar[str | None] = ContextVar("camera_id", default=None)

#: Attributes every ``LogRecord`` has; anything else was passed with ``extra=``.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_HANDLER_NAME = "publicsafe"

access_log = logging.getLogger("publicsafe.access")


def _context(record: logging.LogRecord) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rid = getattr(record, "request_id", None) or request_id_var.get()
    cid = getattr(record, "camera_id", None) or camera_id_var.get()
    if rid:
        out["request_id"] = rid
    if cid:
        out["camera_id"] = cid
    for key, value in vars(record).items():
        if key not in _STANDARD and key not in ("request_id", "camera_id"):
            out[key] = value
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "t": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            **_context(record),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        t = dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        fields = " ".join(f"{k}={v}" for k, v in _context(record).items())
        line = f"{t} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if fields:
            line += f"  [{fields}]"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever ``sys.stderr`` is at emit time (pytest swaps it per test)."""

    @property  # type: ignore[override]
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, _value: Any) -> None:
        pass


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Install one stderr handler on the root logger (idempotent: replaces our previous one)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.get_name() == _HANDLER_NAME:
            root.removeHandler(handler)
    handler = _StderrHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(JsonFormatter() if json_logs else TextFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    # publicsafe.access replaces uvicorn's access line (it adds request id and duration).
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False


@contextlib.contextmanager
def camera_context(camera_id: str | None) -> Iterator[None]:
    """Attach ``camera_id`` to every log line inside the block (this thread / task only)."""
    token = camera_id_var.set(camera_id)
    try:
        yield
    finally:
        camera_id_var.reset(token)


Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name == b"x-request-id":
            text = value.decode("latin-1")
            return text if _REQUEST_ID.fullmatch(text) else None
    return None


class RequestContextMiddleware:
    """Pure-ASGI: request id per HTTP request / WebSocket, ``X-Request-ID`` header, access log."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        rid = _incoming_request_id(scope) or secrets.token_hex(8)
        token = request_id_var.set(rid)
        status: int | None = None
        start = time.perf_counter()

        async def send_with_id(message: MutableMapping[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            if scope["type"] == "http":
                access_log.info(
                    "%s %s %s",
                    scope.get("method"),
                    scope.get("path"),
                    status,
                    extra={
                        "method": scope.get("method"),
                        "path": scope.get("path"),  # never the query string
                        "status": status,
                        "duration_ms": round((time.perf_counter() - start) * 1000.0, 2),
                    },
                )
            request_id_var.reset(token)
