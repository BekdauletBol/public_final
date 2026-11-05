"""M6: structured JSON logs with request and camera ids; X-Request-ID; no query strings."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from publicsafe.logs import JsonFormatter, TextFormatter, camera_context, request_id_var


def record(msg: str = "hello %s", *args: object, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("publicsafe.test", logging.INFO, __file__, 1, msg, args or ("x",), None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_json_line_has_ids_and_extras() -> None:
    token = request_id_var.set("req-1")
    try:
        with camera_context("cam-9"):
            line = JsonFormatter().format(record(seq=42))
    finally:
        request_id_var.reset(token)
    payload = json.loads(line)
    assert payload["msg"] == "hello x" and payload["level"] == "info"
    assert payload["logger"] == "publicsafe.test"
    assert (payload["request_id"], payload["camera_id"], payload["seq"]) == ("req-1", "cam-9", 42)
    assert payload["t"].endswith("+00:00")


def test_context_is_reset_after_the_block() -> None:
    with camera_context("cam-1"):
        pass
    payload = json.loads(JsonFormatter().format(record()))
    assert "camera_id" not in payload and "request_id" not in payload


def test_exceptions_are_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    payload = json.loads(JsonFormatter().format(rec))
    assert "ValueError: boom" in payload["exc"]
    assert "failed" in TextFormatter().format(rec)


def test_request_id_header_and_access_log(tc: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="publicsafe.access")
    r = tc.get("/api/health?secret=do-not-log")
    rid = r.headers["x-request-id"]
    assert len(rid) == 16
    access = [rec for rec in caplog.records if rec.name == "publicsafe.access"]
    assert access, "every HTTP request gets an access line"
    last = access[-1]
    assert (last.method, last.path, last.status) == ("GET", "/api/health", 200)  # type: ignore[attr-defined]
    assert last.duration_ms >= 0  # type: ignore[attr-defined]
    ours = [JsonFormatter().format(r) for r in caplog.records if r.name.startswith("publicsafe")]
    assert ours and not any("do-not-log" in line for line in ours)  # (httpx logs the client URL)
    assert json.loads(JsonFormatter().format(last))["path"] == "/api/health"


def test_incoming_request_id_is_kept_only_if_safe(tc: TestClient) -> None:
    assert (
        tc.get("/api/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"]
        == "abc-123"
    )
    bad = tc.get("/api/health", headers={"X-Request-ID": "x" * 80}).headers["x-request-id"]
    assert bad != "x" * 80 and len(bad) == 16
