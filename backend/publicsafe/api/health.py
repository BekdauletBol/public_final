"""``GET /api/health`` (F.6)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from publicsafe import __version__

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    state = request.app.state
    return {
        "status": "ok",
        "version": __version__,
        "uptime_s": round(time.monotonic() - state.started_at, 1),
        "device": getattr(state, "device", None),
        "model_sha256": getattr(state, "model_sha256", None),
        "db": state.db_status() if callable(getattr(state, "db_status", None)) else None,
    }
