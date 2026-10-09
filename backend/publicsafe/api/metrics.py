"""``GET /api/metrics`` (F.6): current values; percentiles are added by the pipeline (M3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from publicsafe.api.deps import require_admin

router = APIRouter(prefix="/api", tags=["metrics"], dependencies=[Depends(require_admin)])


@router.get("/metrics")
def metrics(request: Request) -> dict[str, Any]:
    return request.app.state.hub.metrics_payload()
