"""Zone runtime: polygons, state machines and needs_review, shared by the API and the worker."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from publicsafe.core.geometry import aspect_changed
from publicsafe.core.rules import RuleId, ZonePolygon
from publicsafe.core.state_machine import ZoneStateMachine
from publicsafe.db.base import session_scope
from publicsafe.db.models import Camera, Zone


@dataclass(eq=False)
class ZoneRuntime:
    zone_id: str
    camera_id: str
    session_id: str
    name: str
    points: list[list[float]]
    frame_w: int
    frame_h: int
    active_rule: RuleId
    enabled: bool
    needs_review: bool
    machine: ZoneStateMachine
    _polygons: dict[tuple[int, int], ZonePolygon] = field(default_factory=dict)

    def polygon_for(self, w: int, h: int) -> ZonePolygon:
        key = (w, h)
        poly = self._polygons.get(key)
        if poly is None:
            poly = ZonePolygon(self.points, w, h)
            self._polygons = {key: poly}  # one frame size at a time is all we need
        return poly

    def apply(self, row: Zone) -> None:
        """Update from a DB row; the state machine keeps its state (F-ZONE-3)."""
        if row.points != self.points:
            self.points = list(row.points)
            self._polygons = {}
        self.name = row.name
        self.frame_w, self.frame_h = row.frame_w, row.frame_h
        self.active_rule = RuleId(row.active_rule)
        self.enabled = row.enabled
        self.needs_review = row.needs_review
        self.machine.configure(row.enter_frames, row.exit_frames, row.enabled)


class ZoneRegistry:
    """Thread-safe map ``camera_id -> [ZoneRuntime]`` refreshed by the API after changes."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        self._lock = threading.Lock()
        self._zones: dict[str, list[ZoneRuntime]] = {}

    @staticmethod
    def _from_row(row: Zone, session_id: str) -> ZoneRuntime:
        return ZoneRuntime(
            zone_id=row.id,
            camera_id=row.camera_id,
            session_id=session_id,
            name=row.name,
            points=list(row.points),
            frame_w=row.frame_w,
            frame_h=row.frame_h,
            active_rule=RuleId(row.active_rule),
            enabled=row.enabled,
            needs_review=row.needs_review,
            machine=ZoneStateMachine(row.enter_frames, row.exit_frames, row.enabled),
        )

    def load_all(self) -> None:
        with session_scope(self.factory) as db:
            rows = db.execute(
                select(Zone, Camera.session_id)
                .join(Camera, Camera.id == Zone.camera_id)
                .where(Zone.deleted_at.is_(None), Camera.status != "revoked")
                .order_by(Zone.created_at)
            ).all()
        fresh: dict[str, list[ZoneRuntime]] = {}
        for row, session_id in rows:
            fresh.setdefault(row.camera_id, []).append(self._from_row(row, session_id))
        with self._lock:
            self._zones = fresh

    def reload(self, camera_id: str) -> None:
        """Re-read one camera's zones; existing machines keep their state."""
        with session_scope(self.factory) as db:
            camera = db.get(Camera, camera_id)
            rows = (
                list(
                    db.scalars(
                        select(Zone)
                        .where(Zone.camera_id == camera_id, Zone.deleted_at.is_(None))
                        .order_by(Zone.created_at)
                    )
                )
                if camera is not None and camera.status != "revoked"
                else []
            )
            session_id = camera.session_id if camera else ""
            with self._lock:
                existing = {z.zone_id: z for z in self._zones.get(camera_id, [])}
                updated: list[ZoneRuntime] = []
                for row in rows:
                    runtime = existing.get(row.id)
                    if runtime is None:
                        runtime = self._from_row(row, session_id)
                    else:
                        runtime.apply(row)
                    updated.append(runtime)
                if updated:
                    self._zones[camera_id] = updated
                else:
                    self._zones.pop(camera_id, None)

    def remove(self, camera_id: str) -> None:
        with self._lock:
            self._zones.pop(camera_id, None)

    def for_camera(self, camera_id: str) -> list[ZoneRuntime]:
        with self._lock:
            return list(self._zones.get(camera_id, ()))

    def mark_needs_review(self, zone: ZoneRuntime) -> None:
        """Flag a zone whose camera now sends a different aspect ratio (F-ZONE-4)."""
        if zone.needs_review:
            return
        zone.needs_review = True
        with session_scope(self.factory) as db:
            row = db.get(Zone, zone.zone_id)
            if row is not None:
                row.needs_review = True

    def check_aspect(self, zone: ZoneRuntime, w: int, h: int) -> bool:
        if not zone.needs_review and aspect_changed(zone.frame_w, zone.frame_h, w, h):
            self.mark_needs_review(zone)
        return zone.needs_review

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {cid: [z.zone_id for z in zones] for cid, zones in self._zones.items()}
