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
from publicsafe.core.mailbox import FrameRateGuard, Scheduler
from publicsafe.core.models import Frame, FrameResult
from publicsafe.core.state_machine import ZoneEvent
from publicsafe.core.timing import ClockOffsetEstimator, now_ms
from publicsafe.db.base import session_scope, utcnow
from publicsafe.db.models import Camera, Event, MetricSample
from publicsafe.worker.stats import CameraStats
from publicsafe.worker.zones import ZoneRuntime

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
    rate_guard: FrameRateGuard = field(default_factory=lambda: FrameRateGuard(10))
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
    queue: asyncio.Queue[dict[str, Any] | bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=512)
    )
    dropped: int = 0
    previews_skipped: int = 0
    last_preview: dict[str, float] = field(default_factory=dict)

    def wants(self, camera_id: str | None) -> bool:
        return camera_id is None or self.camera_ids is None or camera_id in self.camera_ids

    def offer(self, message: dict[str, Any] | bytes) -> bool:
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False

    def preview_due(self, camera_id: str, now: float) -> bool:
        """Throttle previews to ``preview_fps``; skip when the send queue is congested (E.3)."""
        if self.queue.qsize() > 5:
            self.previews_skipped += 1
            return False
        last = self.last_preview.get(camera_id, 0.0)
        if now - last < 1.0 / self.preview_fps:
            return False
        self.last_preview[camera_id] = now
        return True


class Hub:
    """Registry of phone sockets and dashboard subscribers; owns status transitions."""

    def __init__(
        self, factory: sessionmaker[Session], settings: Settings, scheduler: Scheduler | None = None
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.cameras: dict[str, CameraConn] = {}
        self.dashboards: set[DashboardConn] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.started_at = time.monotonic()
        self.scheduler = scheduler or Scheduler(settings.scheduler_strategy)
        self.stats: dict[str, CameraStats] = {}
        self.last_frames: dict[str, bytes] = {}
        self.device: str | None = None
        self.model_sha256: str | None = None
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
        conn.rate_guard = FrameRateGuard(
            float(conn.settings.get("target_fps") or 10),
            abuse_seconds=self.settings.rate_abuse_seconds,
        )
        self.stats_for(conn.camera_id)
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
            self.scheduler.remove(camera_id)
            self.last_frames.pop(camera_id, None)
            if reason in ("revoked", "session_deleted", "replaced"):
                self.stats.pop(camera_id, None)

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
        conn.rate_guard.set_target(float(conn.settings.get("target_fps") or 10))
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

    def announce_status_threadsafe(
        self, camera_id: str, session_id: str, status: str, reason: str | None = None
    ) -> None:
        """Tell dashboards about a status written by a request handler (revoked, paired)."""
        self.broadcast_threadsafe(
            {
                "type": "camera_status",
                "camera_id": camera_id,
                "session_id": session_id,
                "status": status,
                "last_seen_at": None,
                "reason": reason,
            },
            camera_id,
        )

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

    # --- frames: ingest (loop) and publish (worker -> loop) ------------------

    def stats_for(self, camera_id: str) -> CameraStats:
        stats = self.stats.get(camera_id)
        if stats is None:
            stats = CameraStats()
            self.stats[camera_id] = stats
        return stats

    def camera_settings(self, camera_id: str) -> dict[str, Any]:
        """Settings the worker thread applies (``max_long_edge``, ``confidence``, ...)."""
        conn = self.cameras.get(camera_id)
        return dict(conn.settings) if conn is not None else {}

    def ingest(self, conn: CameraConn, meta: dict[str, Any], data: bytes, t_recv_ms: float) -> None:
        """Hand a received frame to the scheduler without decoding on the loop (E.3)."""
        frame = Frame(
            camera_id=conn.camera_id,
            seq=int(meta["seq"]),
            t_phone_ms=float(meta["t_phone_ms"]),
            t_server_recv_ms=t_recv_ms,
            w=int(meta["w"]),
            h=int(meta["h"]),
            jpeg=data,
        )
        replaced = self.scheduler.put(frame)
        self.stats_for(conn.camera_id).on_received(len(data), replaced=replaced)
        self.last_frames[conn.camera_id] = data

    def publish_frame(self, result: FrameResult, jpeg: bytes) -> None:
        """Loop-side fan-out of one processed frame: JSON always, preview unless privacy."""
        camera_id = result.camera_id
        conn = self.cameras.get(camera_id)
        if conn is not None and conn.status == "error":
            self.spawn(self.set_status(camera_id, "online", "recovered", only_from=("error",)))
        offset = conn.offset.offset_ms if conn is not None else None
        self.stats_for(camera_id).on_processed(result, result.t_phone_ms, offset)
        if not self.dashboards:
            return
        message = result.to_json()
        privacy = bool(conn.settings.get("privacy_mode")) if conn is not None else False
        now = time.monotonic()
        for d in self.dashboards:
            if not d.wants(camera_id):
                continue
            d.offer(message)
            if not privacy and d.preview_due(camera_id, now):
                d.offer(
                    {
                        "type": "frame_preview",
                        "camera_id": camera_id,
                        "seq": result.seq,
                        "bytes": len(jpeg),
                    }
                )
                d.offer(jpeg)

    def publish_frame_threadsafe(self, result: FrameResult, jpeg: bytes) -> None:
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self.publish_frame, result, jpeg)

    def on_worker_result(self, frame: Frame, result: FrameResult) -> None:
        """Worker-thread callback (the publish itself happened inside the pipeline)."""

    def on_zone_event(
        self,
        frame: Frame,
        result: FrameResult,
        zone: ZoneRuntime,
        event: ZoneEvent,
        rule_counts: dict[str, int],
    ) -> None:
        """Worker-thread callback: persist the event, then push it to dashboards (F-CV-3/4)."""
        t_server = dt.datetime.fromtimestamp(event.t_ms / 1000.0, tz=dt.UTC)
        with session_scope(self.factory) as db:
            row = Event(
                session_id=zone.session_id,
                camera_id=zone.camera_id,
                zone_id=zone.zone_id,
                zone_name=zone.name,
                type=event.type,
                t_server=t_server,
                frame_seq=frame.seq,
                count_before=event.count_before,
                count_after=event.count_after,
                active_rule=zone.active_rule.value,
                rule_counts=rule_counts,
            )
            db.add(row)
            db.flush()
            message = event_to_json(row)
        # Nested: the Event object has its own ``type`` (zone_entered, ...), see docs/API.md.
        self.broadcast_threadsafe({"type": "event", "event": message}, zone.camera_id)

    def on_worker_error(self, frame: Frame, exc: BaseException) -> None:
        """Worker-thread callback: decode errors are counted; other errors mark the camera."""
        from publicsafe.worker.pipeline import DecodeError

        stats = self.stats_for(frame.camera_id)
        if isinstance(exc, DecodeError):
            stats.on_decode_error()
            return
        stats.on_worker_error()
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(
                self.set_status(
                    frame.camera_id, "error", type(exc).__name__, only_from=("online", "paused")
                ),
                self.loop,
            )

    def live_summary(self) -> dict[str, dict[str, Any]]:
        """Per-camera live numbers for ``CameraSummary`` (F-CAM-3)."""
        out: dict[str, dict[str, Any]] = {}
        for camera_id, stats in self.stats.items():
            snap = stats.snapshot()
            conn = self.cameras.get(camera_id)
            out[camera_id] = {
                "fps_in": snap["fps_in"],
                "fps_processed": snap["fps_processed"],
                "drop_ratio": snap["drop_ratio"],
                "rtt_ms": conn.offset.rtt_ms if conn else None,
                "clock_offset_ms": conn.offset.offset_ms if conn else None,
            }
        return out

    # --- metrics -------------------------------------------------------------

    def metrics_payload(self) -> dict[str, Any]:
        """Current values and percentiles over the recent window (F.6, F-DASH-4)."""
        cameras: dict[str, Any] = {}
        for camera_id, stats in self.stats.items():
            conn = self.cameras.get(camera_id)
            snap = stats.snapshot()
            snap.update(
                {
                    "status": conn.status if conn else "offline",
                    "connected_s": round(time.monotonic() - conn.connected_at, 1) if conn else None,
                    "last_seen_at": iso_ms(conn.last_seen_wall) if conn else None,
                    "rtt_ms": conn.offset.rtt_ms if conn else None,
                    "clock_offset_ms": conn.offset.offset_ms if conn else None,
                    "clock_offset_uncertainty_ms": conn.offset.uncertainty_ms if conn else None,
                    "target_fps": conn.settings.get("target_fps") if conn else None,
                    "privacy_mode": bool(conn.settings.get("privacy_mode")) if conn else None,
                }
            )
            cameras[camera_id] = snap
        return {
            "t_server_ms": now_ms(),
            "server": {
                "version": __version__,
                "uptime_s": round(time.monotonic() - self.started_at, 1),
                "cpu_percent": self._process.cpu_percent(interval=None),
                "mem_mb": round(self._process.memory_info().rss / (1024 * 1024), 1),
                "device": self.device,
                "model_sha256": self.model_sha256,
                "active_cameras": len(self.cameras),
                "dashboards": len(self.dashboards),
                "scheduler_strategy": self.settings.scheduler_strategy,
                "scheduler_backlog": self.scheduler.backlog(),
                "eval_record": self.settings.eval_record,
            },
            "cameras": cameras,
        }

    async def write_metric_samples(self) -> int:
        """Persist one ``MetricSample`` per camera with stats; update last frame size."""
        rows: list[tuple[str, dict[str, Any], float | None, float | None]] = []
        for camera_id, stats in list(self.stats.items()):
            conn = self.cameras.get(camera_id)
            if conn is None and stats.frames_in == 0:
                continue
            rows.append(
                (
                    camera_id,
                    stats.sample(),
                    conn.offset.rtt_ms if conn else None,
                    conn.offset.offset_ms if conn else None,
                )
            )
        if not rows:
            return 0
        cpu = self._process.cpu_percent(interval=None)
        mem = round(self._process.memory_info().rss / (1024 * 1024), 1)
        now = utcnow()

        def _db() -> int:
            written = 0
            with session_scope(self.factory) as db:
                for camera_id, sample, rtt, offset in rows:
                    cam = db.get(Camera, camera_id)
                    if cam is None:
                        continue
                    if sample["last_w"] and sample["last_h"]:
                        cam.last_frame_w, cam.last_frame_h = sample["last_w"], sample["last_h"]
                    db.add(
                        MetricSample(
                            camera_id=camera_id,
                            t=now,
                            window_s=sample["window_s"],
                            fps_in=sample["fps_in"],
                            fps_processed=sample["fps_processed"],
                            dropped_replaced=sample["dropped_replaced"],
                            dropped_ratelimit=sample["dropped_ratelimit"],
                            decode_errors=sample["decode_errors"],
                            out_of_order=sample["out_of_order"],
                            total_p50=sample["total_p50"],
                            total_p95=sample["total_p95"],
                            total_p99=sample["total_p99"],
                            infer_p50=sample["infer_p50"],
                            infer_p95=sample["infer_p95"],
                            queue_wait_p50=sample["queue_wait_p50"],
                            rtt_ms=rtt,
                            clock_offset_ms=offset,
                            e2e_estimate_p50=sample["e2e_estimate_p50"],
                            server_cpu=cpu,
                            server_mem_mb=mem,
                        )
                    )
                    written += 1
            return written

        return await asyncio.to_thread(_db)


def event_to_json(row: Event) -> dict[str, Any]:
    """The ``Event`` object of F.6 (also used by the events API)."""
    return {
        "id": row.id,
        "session_id": row.session_id,
        "camera_id": row.camera_id,
        "zone_id": row.zone_id,
        "zone_name": row.zone_name,
        "type": row.type,
        "t_server": iso_ms(row.t_server),
        "frame_seq": row.frame_seq,
        "count_before": row.count_before,
        "count_after": row.count_after,
        "active_rule": row.active_rule,
        "rule_counts": row.rule_counts or {},
        "created_at": iso_ms(row.created_at),
    }


async def sweep_loop(hub: Hub, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            await hub.sweep()
        except Exception:
            log.exception("sweeper failed")


async def sample_loop(hub: Hub, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            await hub.write_metric_samples()
        except Exception:
            log.exception("metric sample write failed")


async def metrics_loop(hub: Hub, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        if hub.dashboards:
            try:
                hub.broadcast({"type": "metrics", **hub.metrics_payload()})
            except Exception:
                log.exception("metrics broadcast failed")
