"""Cameras (F-CAM-*, F.4). Live metrics fields are filled from M3 on."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from publicsafe.api.deps import get_db, require_admin
from publicsafe.api.errors import ApiError
from publicsafe.api.ratelimit import client_ip
from publicsafe.api.schemas import (
    CameraDetail,
    CameraPatch,
    CameraSettings,
    CameraSummary,
    RevokeIn,
    ZoneOut,
)
from publicsafe.db.models import Camera
from publicsafe.db.repo import (
    active_zones,
    get_camera_or_404,
    log_security,
    revoke_credentials,
    zone_stats,
)

router = APIRouter(prefix="/api/cameras", tags=["cameras"], dependencies=[Depends(require_admin)])


def camera_summaries(db: Session, cameras: list[Camera]) -> list[CameraSummary]:
    stats = zone_stats(db, [c.id for c in cameras])
    out: list[CameraSummary] = []
    for camera in cameras:
        zone_count, needs_review = stats.get(camera.id, (0, False))
        out.append(
            CameraSummary(
                id=camera.id,
                session_id=camera.session_id,
                name=camera.name,
                status=camera.status,
                device_label=camera.device_label,
                created_at=camera.created_at,
                last_seen_at=camera.last_seen_at,
                zone_count=zone_count,
                needs_review=needs_review,
            )
        )
    return out


def camera_detail(db: Session, camera: Camera) -> CameraDetail:
    summary = camera_summaries(db, [camera])[0]
    zones = [ZoneOut.model_validate(z) for z in active_zones(db, camera.id)]
    return CameraDetail(
        **summary.model_dump(),
        settings=CameraSettings.model_validate(camera.settings),
        zones=zones,
        health={},
    )


@router.get("", response_model=list[CameraSummary])
def list_cameras(
    session_id: str | None = None, db: Session = Depends(get_db)
) -> list[CameraSummary]:
    stmt = select(Camera).order_by(Camera.created_at)
    if session_id is not None:
        stmt = stmt.where(Camera.session_id == session_id)
    return camera_summaries(db, list(db.scalars(stmt)))


@router.get("/{camera_id}", response_model=CameraDetail)
def get_camera(camera_id: str, db: Session = Depends(get_db)) -> CameraDetail:
    return camera_detail(db, get_camera_or_404(db, camera_id))


@router.patch("/{camera_id}", response_model=CameraDetail)
def patch_camera(
    camera_id: str, body: CameraPatch, request: Request, db: Session = Depends(get_db)
) -> CameraDetail:
    camera = get_camera_or_404(db, camera_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("name"):
        camera.name = data["name"]
    if body.settings is not None:
        merged = {**camera.settings, **body.settings.model_dump(exclude_unset=True)}
        camera.settings = CameraSettings.model_validate(merged).model_dump()
        pusher = getattr(request.app.state, "push_camera_settings", None)
        if pusher is not None:  # installed by the WebSocket layer (M2)
            pusher(camera.id, camera.settings)
    db.flush()
    return camera_detail(db, camera)


@router.post("/{camera_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_camera(
    camera_id: str, request: Request, body: RevokeIn | None = None, db: Session = Depends(get_db)
) -> Response:
    camera = get_camera_or_404(db, camera_id)
    now = dt.datetime.now(dt.UTC)
    reason = (body.reason if body else None) or "admin_revoked"
    revoke_credentials(db, camera.id, reason, now)
    camera.status = "revoked"
    camera.revoked_at = now
    log_security(
        db,
        "token_revoked",
        client_ip(request),
        camera_id=camera.id,
        session_id=camera.session_id,
        details={"reason": reason},
    )
    db.flush()
    closer = getattr(request.app.state, "close_camera_sockets", None)
    if closer is not None:
        closer([camera.id], 4003, "revoked")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: str, db: Session = Depends(get_db)) -> Response:
    camera = get_camera_or_404(db, camera_id)
    if camera.status != "revoked" and camera.last_seen_at is not None:
        raise ApiError(409, "camera_active", "Revoke the camera before deleting it")
    db.delete(camera)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{camera_id}/last-frame.jpg")
def last_frame(camera_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    camera = get_camera_or_404(db, camera_id)
    if camera.settings.get("privacy_mode"):
        raise ApiError(404, "privacy_mode", "Frames are not available in privacy mode")
    frames = getattr(request.app.state, "last_frames", None)  # filled by the ingest layer (M3)
    jpeg = frames.get(camera.id) if frames is not None else None
    if not jpeg:
        raise ApiError(404, "no_frame", "No frame received from this camera yet")
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
