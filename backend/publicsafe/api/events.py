"""Events (F.6): filtered listing with cursor paging and CSV export (K)."""

from __future__ import annotations

import base64
import csv
import datetime as dt
import io
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from publicsafe.api.deps import get_db, require_admin
from publicsafe.api.errors import ApiError
from publicsafe.api.metrics import parse_ts
from publicsafe.api.schemas import iso_ms
from publicsafe.core.rules import ALL_RULES
from publicsafe.db.models import Camera, Event, MonitoringSession
from publicsafe.ws.hub import event_to_json

router = APIRouter(prefix="/api/events", tags=["events"], dependencies=[Depends(require_admin)])

EVENT_TYPES = ("zone_entered", "zone_cleared", "occupancy_changed")
CSV_COLUMNS = [
    "id",
    "session_id",
    "session_name",
    "camera_id",
    "camera_name",
    "zone_id",
    "zone_name",
    "type",
    "t_server",
    "frame_seq",
    "count_before",
    "count_after",
    "active_rule",
    *[rule.value for rule in ALL_RULES],
]


def _encode_cursor(row: Event) -> str:
    raw = f"{row.t_server.timestamp():.6f}|{row.id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[dt.datetime, str]:
    try:
        ts, _, row_id = base64.urlsafe_b64decode(cursor.encode("ascii")).decode().partition("|")
        return dt.datetime.fromtimestamp(float(ts), tz=dt.UTC), row_id
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "validation_error", "cursor is not valid") from None


def _filtered(
    session_id: str | None,
    camera_id: str | None,
    zone_id: str | None,
    type_: str | None,
    from_: str | None,
    to: str | None,
) -> Select[tuple[Event]]:
    stmt = select(Event)
    if session_id:
        stmt = stmt.where(Event.session_id == session_id)
    if camera_id:
        stmt = stmt.where(Event.camera_id == camera_id)
    if zone_id:
        stmt = stmt.where(Event.zone_id == zone_id)
    if type_:
        if type_ not in EVENT_TYPES:
            raise ApiError(400, "validation_error", f"type must be one of {EVENT_TYPES}")
        stmt = stmt.where(Event.type == type_)
    start, end = parse_ts(from_, "from"), parse_ts(to, "to")
    if start:
        stmt = stmt.where(Event.t_server >= start)
    if end:
        stmt = stmt.where(Event.t_server <= end)
    return stmt


@router.get("")
def list_events(
    session_id: str | None = None,
    camera_id: str | None = None,
    zone_id: str | None = None,
    type: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Newest first; ``next_cursor`` continues after the last returned row."""
    stmt = _filtered(session_id, camera_id, zone_id, type, from_, to)
    if cursor:
        before_t, before_id = _decode_cursor(cursor)
        stmt = stmt.where(
            (Event.t_server < before_t) | ((Event.t_server == before_t) & (Event.id < before_id))
        )
    rows = list(db.scalars(stmt.order_by(Event.t_server.desc(), Event.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [event_to_json(r) for r in rows],
        "next_cursor": _encode_cursor(rows[-1]) if has_more and rows else None,
    }


@router.get("/export.csv")
def export_events(
    session_id: str | None = None,
    camera_id: str | None = None,
    zone_id: str | None = None,
    type: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Columns per Section K: ids, names, event fields, then one column per rule."""
    stmt = (
        _filtered(session_id, camera_id, zone_id, type, from_, to)
        .add_columns(MonitoringSession.name, Camera.name)
        .join(MonitoringSession, MonitoringSession.id == Event.session_id)
        .join(Camera, Camera.id == Event.camera_id)
        .order_by(Event.t_server, Event.id)
    )
    rows = db.execute(stmt).all()

    def generate() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        yield buf.getvalue()
        for event, session_name, camera_name in rows:
            buf.seek(0)
            buf.truncate()
            counts = event.rule_counts or {}
            writer.writerow(
                [
                    event.id,
                    event.session_id,
                    session_name,
                    event.camera_id,
                    camera_name,
                    event.zone_id,
                    event.zone_name,
                    event.type,
                    iso_ms(event.t_server),
                    event.frame_seq,
                    event.count_before,
                    event.count_after,
                    event.active_rule,
                    *[counts.get(rule.value, "") for rule in ALL_RULES],
                ]
            )
            yield buf.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="events.csv"'},
    )
