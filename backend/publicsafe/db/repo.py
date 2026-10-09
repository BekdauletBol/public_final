"""Typed repository helpers shared by the API and the worker."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from publicsafe.db.models import Camera, DeviceCredential, MonitoringSession, SecurityLog, Zone


def log_security(
    db: Session,
    kind: str,
    ip: str | None,
    *,
    camera_id: str | None = None,
    session_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SecurityLog:
    """Append a security-log row. ``details`` must never contain PINs or tokens."""
    row = SecurityLog(kind=kind, ip=ip, camera_id=camera_id, session_id=session_id, details=details)
    db.add(row)
    db.flush()
    return row


def camera_counts(db: Session) -> dict[str, tuple[int, int]]:
    """``session_id -> (camera_count, online_count)`` over non-revoked cameras."""
    rows = db.execute(
        select(
            Camera.session_id,
            func.count(Camera.id),
            func.count(Camera.id).filter(Camera.status == "online"),
        )
        .where(Camera.status != "revoked")
        .group_by(Camera.session_id)
    ).all()
    return {sid: (int(total), int(on or 0)) for sid, total, on in rows}


def get_session_or_404(db: Session, session_id: str) -> MonitoringSession:
    from publicsafe.api.errors import ApiError

    row = db.get(MonitoringSession, session_id)
    if row is None:
        raise ApiError(404, "session_not_found", "Session not found")
    return row


def get_camera_or_404(db: Session, camera_id: str) -> Camera:
    from publicsafe.api.errors import ApiError

    row = db.get(Camera, camera_id)
    if row is None:
        raise ApiError(404, "camera_not_found", "Camera not found")
    return row


def get_zone_or_404(db: Session, zone_id: str) -> Zone:
    from publicsafe.api.errors import ApiError

    row = db.get(Zone, zone_id)
    if row is None or row.deleted_at is not None:
        raise ApiError(404, "zone_not_found", "Zone not found")
    return row


def active_zones(db: Session, camera_id: str) -> list[Zone]:
    return list(
        db.scalars(
            select(Zone)
            .where(Zone.camera_id == camera_id, Zone.deleted_at.is_(None))
            .order_by(Zone.created_at)
        )
    )


def zone_stats(db: Session, camera_ids: list[str]) -> dict[str, tuple[int, bool]]:
    """``camera_id -> (zone_count, any_needs_review)`` over non-deleted zones."""
    if not camera_ids:
        return {}
    rows = db.execute(
        select(
            Zone.camera_id,
            func.count(Zone.id),
            func.count(Zone.id).filter(Zone.needs_review.is_(True)),
        )
        .where(Zone.camera_id.in_(camera_ids), Zone.deleted_at.is_(None))
        .group_by(Zone.camera_id)
    ).all()
    return {cid: (int(n), bool(review)) for cid, n, review in rows}


def revoke_credentials(db: Session, camera_id: str, reason: str, now: dt.datetime) -> int:
    """Revoke every active credential of a camera; returns how many were revoked."""
    rows = list(
        db.scalars(
            select(DeviceCredential).where(
                DeviceCredential.camera_id == camera_id, DeviceCredential.revoked_at.is_(None)
            )
        )
    )
    for cred in rows:
        cred.revoked_at = now
        cred.revoked_reason = reason
    db.flush()
    return len(rows)
