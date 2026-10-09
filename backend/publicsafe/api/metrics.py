"""``GET /api/metrics`` (F.6): current values; percentiles are added by the pipeline (M3)."""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from publicsafe.api.deps import get_db, require_admin
from publicsafe.api.errors import ApiError
from publicsafe.api.schemas import iso_ms
from publicsafe.db.models import Camera, MetricSample

router = APIRouter(prefix="/api", tags=["metrics"], dependencies=[Depends(require_admin)])

METRIC_COLUMNS = [
    "id",
    "camera_id",
    "camera_name",
    "t",
    "window_s",
    "fps_in",
    "fps_processed",
    "dropped_replaced",
    "dropped_ratelimit",
    "decode_errors",
    "out_of_order",
    "total_p50",
    "total_p95",
    "total_p99",
    "infer_p50",
    "infer_p95",
    "queue_wait_p50",
    "rtt_ms",
    "clock_offset_ms",
    "e2e_estimate_p50",
    "server_cpu",
    "server_mem_mb",
]


def parse_ts(value: str | None, name: str) -> dt.datetime | None:
    if value is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "validation_error", f"{name} must be an ISO timestamp") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


@router.get("/metrics")
def metrics(request: Request) -> dict[str, Any]:
    return request.app.state.hub.metrics_payload()


@router.get("/metrics/export.csv")
def export_metrics(
    camera_id: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """All ``metric_samples`` columns plus ``camera_name`` (K)."""
    start, end = parse_ts(from_, "from"), parse_ts(to, "to")
    stmt = (
        select(MetricSample, Camera.name)
        .join(Camera, Camera.id == MetricSample.camera_id)
        .order_by(MetricSample.t, MetricSample.camera_id)
    )
    if camera_id:
        stmt = stmt.where(MetricSample.camera_id == camera_id)
    if start:
        stmt = stmt.where(MetricSample.t >= start)
    if end:
        stmt = stmt.where(MetricSample.t <= end)
    rows = db.execute(stmt).all()

    def generate() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(METRIC_COLUMNS)
        yield buf.getvalue()
        for sample, name in rows:
            buf.seek(0)
            buf.truncate()
            writer.writerow(
                [
                    sample.id,
                    sample.camera_id,
                    name,
                    iso_ms(sample.t),
                    *(getattr(sample, col) for col in METRIC_COLUMNS[4:]),
                ]
            )
            yield buf.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="metrics.csv"'},
    )
