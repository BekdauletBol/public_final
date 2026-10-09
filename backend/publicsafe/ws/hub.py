"""Connection registry, camera status transitions and dashboard fan-out (E.2, E.3)."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import os
import time
from collections.abc import Coroutine, Iterable
from dataclasses import dataclass, field
from typing import Any

import psutil
from fastapi import WebSocket
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from publicsafe import __version__
from publicsafe.api.schemas import iso_ms
from publicsafe.config import Settings
from publicsafe.core.timing import ClockOffsetEstimator, now_ms
from publicsafe.db.base import session_scope, utcnow
from publicsafe.db.models import Camera

log = logging.getLogger("publicsafe.ws")

#: Statuses a live socket may leave by disconnecting or timing out.
LIVE_STATUSES = ("online", "paused", "error")


@dataclass(eq=False)
class CameraConn:
    """Server-side state of one phone socket (F.7)."""

    camera_id: str
    session_id: str
    camera_name: str
    settings: dict[str, Any]
    ws: WebSocket
    ip: str | None
    connected_at: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    last_seen_wall: dt.datetime = field(default_factory=utcnow)
    status: str = "online"
    frames_in: int = 0
    bytes_in: int = 0
    last_seq: int = -1
    out_of_order: int = 0
    pending_meta: dict[str, Any] | None = None
    offset: ClockOffsetEstimator = field(default_factory=ClockOffsetEstimator)
    closing: bool = False
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.last_seen = time.monotonic()
        self.last_seen_wall = utcnow()

    async def send_json(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send_json(message)

    async def close(self, code: int, reason: str, message: str | None = None) -> None:
        """Send an ``error`` then close; idempotent and never raises."""
        if self.closing:
            return
        self.closing = True
        with contextlib.suppress(Exception):
            await self.send_json(
                {"type": "error", "code": reason, "message": message or reason.replace("_", " ")}
            )
        with contextlib.suppress(Exception):
            await self.ws.close(code=code, reason=reason)


@dataclass(eq=False)
class DashboardConn:
    """One browser subscription (F.8)."""

    ws: WebSocket
    camera_ids: set[str] | None = None  # None means "all"
    preview_fps: int = 10
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=512))
    dropped: int = 0

    def wants(self, camera_id: str | None) -> bool:
        return camera_id is None or self.camera_ids is None or camera_id in self.camera_ids

    def offer(self, message: dict[str, Any]) -> bool:
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False


class Hub:
    """Registry of phone sockets and dashboard subscribers; owns status transitions."""

    def __init__(self, factory: sessionmaker[Session], settings: Settings) -> None:
        self.factory = factory
        self.settings = settings
        self.cameras: dict[str, CameraConn] = {}
        self.dashboards: set[DashboardConn] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.started_at = time.monotonic()
        self.last_frames: dict[str, bytes] = {}  # filled by the ingest layer (M3)
        self._process = psutil.Process(os.getpid())
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run ``coro`` as a hub-owned task that survives the caller's cancellation."""
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- lifecycle -----------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def mark_live_cameras_offline(self) -> None:
        """At start-up no socket exists; statuses left by a previous run are stale."""

        def _db() -> None:
            with session_scope(self.factory) as db:
                db.execute(
                    update(Camera)
                    .where(Camera.status.in_(("online", "paused", "error")))
                    .values(status="offline")
                )

        await asyncio.to_thread(_db)

    async def close_all(self, code: int = 1001, reason: str = "server_shutdown") -> None:
        for task in list(self._tasks):
            if not task.done():
                await task
        await self.close_cameras(list(self.cameras), code, reason)
        for d in list(self.dashboards):
            with contextlib.suppress(Exception):
                await d.ws.close(code=code, reason=reason)

    # --- camera registry -----------------------------------------------------

    async def attach(self, conn: CameraConn) -> CameraConn | None:
        """Register ``conn``; a previous socket for the camera is closed with 4004."""
        old = self.cameras.get(conn.camera_id)
        self.cameras[conn.camera_id] = conn
        if old is not None:
            await old.close(4004, "superseded", "Another connection took over this camera")
        return old

    def detach(self, conn: CameraConn) -> bool:
        if self.cameras.get(conn.camera_id) is conn:
            del self.cameras[conn.camera_id]
            return True
        return False

    def get(self, camera_id: str) -> CameraConn | None:
        return self.cameras.get(camera_id)

    async def close_cameras(self, camera_ids: Iterable[str], code: int, reason: str) -> None:
        for camera_id in list(camera_ids):
            conn = self.cameras.pop(camera_id, None)
            if conn is not None:
                await conn.close(code, reason)

    def close_cameras_threadsafe(self, camera_ids: Iterable[str], code: int, reason: str) -> None:
        """For sync request handlers (threadpool): schedule the close on the loop."""
        ids = list(camera_ids)
        if self.loop is None or not ids:
            return
        asyncio.run_coroutine_threadsafe(self.close_cameras(ids, code, reason), self.loop)

    async def push_settings(self, camera_id: str, settings: dict[str, Any]) -> None:
        conn = self.cameras.get(camera_id)
        if conn is None:
            return
        conn.settings = dict(settings)
        try:
            await conn.send_json({"type": "settings", "settings": conn.settings})
        except Exception:
            log.info("settings push failed camera=%s", camera_id)

    def push_settings_threadsafe(self, camera_id: str, settings: dict[str, Any]) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.push_settings(camera_id, settings), self.loop)

    # --- dashboards ----------------------------------------------------------

    def add_dashboard(self, conn: DashboardConn) -> None:
        self.dashboards.add(conn)

    def remove_dashboard(self, conn: DashboardConn) -> None:
        self.dashboards.discard(conn)

    def broadcast(self, message: dict[str, Any], camera_id: str | None = None) -> None:
        for d in self.dashboards:
            if d.wants(camera_id):
                d.offer(message)

    def broadcast_threadsafe(self, message: dict[str, Any], camera_id: str | None = None) -> None:
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self.broadcast, message, camera_id)

    # --- status --------------------------------------------------------------

    async def set_status(
        self,
        camera_id: str,
        status: str,
        reason: str | None = None,
        *,
        only_from: tuple[str, ...] | None = None,
        touch: bool = True,
    ) -> bool:
        """Persist a status change and tell dashboards. Never overrides ``revoked``."""
        now = utcnow()

        def _db() -> tuple[str, dt.datetime | None] | None:
            with session_scope(self.factory) as db:
                cam = db.get(Camera, camera_id)
                if cam is None or cam.status == "revoked":
                    return None
                if only_from is not None and cam.status not in only_from:
                    return None
                cam.status = status
                if touch:
                    cam.last_seen_at = now
                return cam.session_id, cam.last_seen_at

        result = await asyncio.to_thread(_db)
        if result is None:
            return False
        session_id, last_seen = result
        conn = self.cameras.get(camera_id)
        if conn is not None:
            conn.status = status
        self.broadcast(
            {
                "type": "camera_status",
                "camera_id": camera_id,
                "session_id": session_id,
                "status": status,
                "last_seen_at": iso_ms(last_seen) if last_seen else None,
                "reason": reason,
            },
            camera_id,
        )
        return True

    async def status_snapshot(self, camera_ids: set[str] | None) -> list[dict[str, Any]]:
        def _db() -> list[dict[str, Any]]:
            with session_scope(self.factory) as db:
                stmt = select(Camera)
                if camera_ids is not None:
                    stmt = stmt.where(Camera.id.in_(camera_ids))
                return [
                    {
                        "type": "camera_status",
                        "camera_id": cam.id,
                        "session_id": cam.session_id,
                        "status": cam.status,
                        "last_seen_at": iso_ms(cam.last_seen_at) if cam.last_seen_at else None,
                        "reason": "snapshot",
                    }
                    for cam in db.scalars(stmt)
                ]

        return await asyncio.to_thread(_db)

    async def sweep(self) -> None:
        """Mark cameras without a frame or ping for ``offline_after_s`` as offline."""
        now = time.monotonic()
        for conn in list(self.cameras.values()):
            if conn.status in ("online", "paused", "error") and (
                now - conn.last_seen > self.settings.offline_after_s
            ):
                await self.set_status(
                    conn.camera_id, "offline", "timeout", only_from=LIVE_STATUSES, touch=False
                )

    # --- metrics -------------------------------------------------------------

    def metrics_payload(self) -> dict[str, Any]:
        """Current values; percentiles over the last 60 s arrive with the pipeline (M3)."""
        cameras: dict[str, Any] = {}
        for camera_id, conn in self.cameras.items():
            cameras[camera_id] = {
                "status": conn.status,
                "connected_s": round(time.monotonic() - conn.connected_at, 1),
                "last_seen_at": iso_ms(conn.last_seen_wall),
                "frames_in": conn.frames_in,
                "bytes_in": conn.bytes_in,
                "out_of_order": conn.out_of_order,
                "rtt_ms": conn.offset.rtt_ms,
                "clock_offset_ms": conn.offset.offset_ms,
                "clock_offset_uncertainty_ms": conn.offset.uncertainty_ms,
                "fps_in": None,
                "fps_processed": None,
                "drop_ratio": None,
                "spans": None,
            }
        return {
            "t_server_ms": now_ms(),
            "server": {
                "version": __version__,
                "uptime_s": round(time.monotonic() - self.started_at, 1),
                "cpu_percent": self._process.cpu_percent(interval=None),
                "mem_mb": round(self._process.memory_info().rss / (1024 * 1024), 1),
                "device": None,
                "active_cameras": len(self.cameras),
                "dashboards": len(self.dashboards),
                "scheduler_strategy": self.settings.scheduler_strategy,
                "eval_record": self.settings.eval_record,
            },
            "cameras": cameras,
        }


async def sweep_loop(hub: Hub, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            await hub.sweep()
        except Exception:
            log.exception("sweeper failed")


async def metrics_loop(hub: Hub, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        if hub.dashboards:
            try:
                hub.broadcast({"type": "metrics", **hub.metrics_payload()})
            except Exception:
                log.exception("metrics broadcast failed")
