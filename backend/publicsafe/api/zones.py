"""Zones (F-ZONE-*, F.5)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from publicsafe.api.deps import get_db, require_admin
from publicsafe.api.errors import ApiError
from publicsafe.api.schemas import ZoneCreate, ZoneOut, ZonePatch
from publicsafe.core.geometry import PolygonError, validate_polygon
from publicsafe.db.models import Zone
from publicsafe.db.repo import active_zones, get_camera_or_404, get_zone_or_404

router = APIRouter(prefix="/api", tags=["zones"], dependencies=[Depends(require_admin)])


def _validated_points(points: list[list[float]]) -> list[list[float]]:
    try:
        return [[x, y] for x, y in validate_polygon(points)]
    except PolygonError as exc:
        raise ApiError(400, "polygon_invalid", exc.message, {"reason": exc.reason}) from None


@router.get("/cameras/{camera_id}/zones", response_model=list[ZoneOut])
def list_zones(camera_id: str, db: Session = Depends(get_db)) -> list[ZoneOut]:
    get_camera_or_404(db, camera_id)
    return [ZoneOut.model_validate(z) for z in active_zones(db, camera_id)]


def _sync_runtime(request: Request, db: Session, camera_id: str) -> None:
    """Commit, then refresh the worker's view of this camera's zones (F-ZONE-3)."""
    db.commit()
    request.app.state.zones.reload(camera_id)


@router.post(
    "/cameras/{camera_id}/zones", response_model=ZoneOut, status_code=status.HTTP_201_CREATED
)
def create_zone(
    camera_id: str, body: ZoneCreate, request: Request, db: Session = Depends(get_db)
) -> ZoneOut:
    camera = get_camera_or_404(db, camera_id)
    zone = Zone(
        camera_id=camera.id,
        name=body.name,
        points=_validated_points(body.points),
        frame_w=body.frame_w,
        frame_h=body.frame_h,
        active_rule=body.active_rule.value,
        enter_frames=body.enter_frames,
        exit_frames=body.exit_frames,
        enabled=body.enabled,
    )
    db.add(zone)
    db.flush()
    out = ZoneOut.model_validate(zone)
    _sync_runtime(request, db, camera.id)
    return out


@router.patch("/zones/{zone_id}", response_model=ZoneOut)
def patch_zone(
    zone_id: str, body: ZonePatch, request: Request, db: Session = Depends(get_db)
) -> ZoneOut:
    zone = get_zone_or_404(db, zone_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("points") is not None:
        zone.points = _validated_points(data["points"])
    for field in ("name", "frame_w", "frame_h", "enter_frames", "exit_frames", "enabled"):
        if data.get(field) is not None:
            setattr(zone, field, data[field])
    if body.active_rule is not None:
        zone.active_rule = body.active_rule.value  # state machine keeps its state (F-ZONE-3)
    if data.get("points") is not None or "frame_w" in data or "frame_h" in data:
        zone.needs_review = False  # re-drawn on the current frame size
    zone.updated_at = dt.datetime.now(dt.UTC)
    db.flush()
    out = ZoneOut.model_validate(zone)
    _sync_runtime(request, db, zone.camera_id)
    return out


@router.delete("/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_zone(zone_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    zone = get_zone_or_404(db, zone_id)
    zone.deleted_at = dt.datetime.now(dt.UTC)
    db.flush()
    _sync_runtime(request, db, zone.camera_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
