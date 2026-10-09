"""Monitoring sessions (F-SES-1, F.2)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from publicsafe.api.cameras import camera_summaries
from publicsafe.api.deps import AdminContext, get_db, require_admin
from publicsafe.api.schemas import SessionCreate, SessionDetail, SessionOut, SessionPatch
from publicsafe.db.models import Camera, MonitoringSession
from publicsafe.db.repo import camera_counts, get_session_or_404, revoke_credentials

router = APIRouter(prefix="/api/sessions", tags=["sessions"], dependencies=[Depends(require_admin)])


def _out(row: MonitoringSession, counts: dict[str, tuple[int, int]]) -> SessionOut:
    total, online = counts.get(row.id, (0, 0))
    return SessionOut(
        id=row.id,
        name=row.name,
        location_note=row.location_note,
        created_at=row.created_at,
        archived_at=row.archived_at,
        camera_count=total,
        online_count=online,
    )


@router.get("", response_model=list[SessionOut])
def list_sessions(
    include_archived: bool = False, db: Session = Depends(get_db)
) -> list[SessionOut]:
    stmt = select(MonitoringSession).order_by(MonitoringSession.created_at.desc())
    if not include_archived:
        stmt = stmt.where(MonitoringSession.archived_at.is_(None))
    counts = camera_counts(db)
    return [_out(row, counts) for row in db.scalars(stmt)]


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(body: SessionCreate, db: Session = Depends(get_db)) -> SessionOut:
    row = MonitoringSession(name=body.name, location_note=body.location_note)
    db.add(row)
    db.flush()
    return _out(row, {})


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: str, db: Session = Depends(get_db)) -> SessionDetail:
    row = get_session_or_404(db, session_id)
    cameras = list(
        db.scalars(select(Camera).where(Camera.session_id == row.id).order_by(Camera.created_at))
    )
    base = _out(row, camera_counts(db))
    return SessionDetail(**base.model_dump(), cameras=camera_summaries(db, cameras))


@router.patch("/{session_id}", response_model=SessionOut)
def patch_session(session_id: str, body: SessionPatch, db: Session = Depends(get_db)) -> SessionOut:
    row = get_session_or_404(db, session_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        row.name = data["name"]
    if "location_note" in data:
        row.location_note = data["location_note"]
    if data.get("archived") is True and row.archived_at is None:
        row.archived_at = dt.datetime.now(dt.UTC)
    elif data.get("archived") is False:
        row.archived_at = None
    db.flush()
    return _out(row, camera_counts(db))


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    request: Request,
    admin: AdminContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Hard-delete the session and everything under it (DB cascades); revoke tokens."""
    row = get_session_or_404(db, session_id)
    now = dt.datetime.now(dt.UTC)
    camera_ids = [c.id for c in row.cameras]
    for camera_id in camera_ids:
        revoke_credentials(db, camera_id, "session_deleted", now)
    db.delete(row)
    db.flush()
    closer = getattr(request.app.state, "close_camera_sockets", None)
    if closer is not None:  # installed by the WebSocket layer (M2)
        closer(camera_ids, 4003, "session_deleted")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
