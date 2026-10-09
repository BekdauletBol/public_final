"""``/ws/camera``: phone ingest socket (F.7, G.3). Frame processing itself arrives in M3."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from publicsafe.core.timing import now_ms
from publicsafe.db.base import session_scope, utcnow
from publicsafe.db.models import Camera, DeviceCredential, MonitoringSession
from publicsafe.db.repo import log_security
from publicsafe.pairing import hash_device_token
from publicsafe.ws.hub import LIVE_STATUSES, CameraConn, Hub

log = logging.getLogger("publicsafe.ws.camera")
router = APIRouter()

Number = int | float


@dataclass(frozen=True)
class CameraIdentity:
    camera_id: str
    session_id: str
    camera_name: str
    settings: dict[str, Any]


def _authenticate(
    factory: sessionmaker[Session], token: str, ip: str | None
) -> CameraIdentity | str:
    """Resolve a device token; returns the identity or a failure reason string."""
    if not token or len(token) > 256:
        return "unknown_token"
    token_hash = hash_device_token(token)
    with session_scope(factory) as db:
        cred = db.scalar(select(DeviceCredential).where(DeviceCredential.token_hash == token_hash))
        if cred is None:
            log_security(db, "ws_auth_failed", ip, details={"reason": "unknown_token"})
            return "unknown_token"
        camera = db.get(Camera, cred.camera_id)
        session = db.get(MonitoringSession, camera.session_id) if camera else None
        if camera is None or session is None:
            log_security(db, "ws_auth_failed", ip, details={"reason": "orphan_credential"})
            return "unknown_token"
        if cred.revoked_at is not None or camera.status == "revoked" or camera.revoked_at:
            log_security(
                db, "ws_auth_failed", ip, camera_id=camera.id, details={"reason": "revoked"}
            )
            return "revoked"
        if session.archived_at is not None:
            log_security(
                db,
                "ws_auth_failed",
                ip,
                camera_id=camera.id,
                session_id=session.id,
                details={"reason": "session_archived"},
            )
            return "session_archived"
        cred.last_used_at = utcnow()
        return CameraIdentity(camera.id, session.id, camera.name, dict(camera.settings))


def _parse(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("type"), str) else None


async def _reject(ws: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await ws.send_json({"type": "error", "code": reason, "message": reason.replace("_", " ")})
    with contextlib.suppress(Exception):
        await ws.close(code=code, reason=reason)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


@router.websocket("/ws/camera")
async def camera_socket(ws: WebSocket) -> None:
    await ws.accept()
    app = ws.app
    settings = app.state.settings
    hub: Hub = app.state.hub
    factory = app.state.sessionmaker
    ip = ws.client.host if ws.client else None

    try:
        first = await asyncio.wait_for(ws.receive(), timeout=settings.ws_auth_timeout_s)
    except TimeoutError:
        await _reject(ws, 4001, "auth_timeout")
        return
    if first.get("type") == "websocket.disconnect":
        return
    auth = _parse(first.get("text"))
    if auth is None or auth.get("type") != "auth" or not isinstance(auth.get("token"), str):
        await asyncio.to_thread(_log_malformed, factory, ip)
        await _reject(ws, 4001, "auth_invalid")
        return

    identity = await asyncio.to_thread(_authenticate, factory, auth["token"], ip)
    if isinstance(identity, str):
        code = 4003 if identity in ("revoked", "session_archived") else 4001
        await _reject(ws, code, identity)
        return

    conn = CameraConn(
        camera_id=identity.camera_id,
        session_id=identity.session_id,
        camera_name=identity.camera_name,
        settings=identity.settings,
        ws=ws,
        ip=ip,
    )
    old = await hub.attach(conn)
    if old is not None:
        await asyncio.to_thread(_log_superseded, factory, ip, conn.camera_id, conn.session_id)
    await hub.set_status(conn.camera_id, "online", "connected")
    await conn.send_json(
        {
            "type": "hello",
            "camera_id": conn.camera_id,
            "camera_name": conn.camera_name,
            "settings": conn.settings,
            "server_time_ms": now_ms(),
            "ack_every": settings.ack_every,
        }
    )

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            data = message.get("bytes")
            if text is not None:
                if not await _handle_text(conn, text, hub):
                    break
            elif data is not None and not await _handle_binary(
                conn, data, hub, settings.max_frame_bytes, settings.ack_every
            ):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("camera socket failed camera=%s", conn.camera_id)
        await conn.close(1011, "server_error")
    finally:
        detached = hub.detach(conn)
        if detached and not conn.closing:
            # Hub-owned task: completes even if this handler is being cancelled.
            hub.spawn(
                hub.set_status(
                    conn.camera_id, "offline", "disconnected", only_from=LIVE_STATUSES, touch=False
                )
            )


def _log_malformed(factory: sessionmaker[Session], ip: str | None) -> None:
    with session_scope(factory) as db:
        log_security(db, "ws_auth_failed", ip, details={"reason": "malformed_auth"})


def _log_superseded(
    factory: sessionmaker[Session], ip: str | None, camera_id: str, session_id: str
) -> None:
    with session_scope(factory) as db:
        log_security(db, "ws_superseded", ip, camera_id=camera_id, session_id=session_id)


async def _handle_text(conn: CameraConn, text: str, hub: Hub) -> bool:
    msg = _parse(text)
    if msg is None:
        await conn.close(4000, "bad_json")
        return False
    kind = msg["type"]
    conn.touch()

    if kind == "frame_meta":
        seq, t_phone, w, h = msg.get("seq"), msg.get("t_phone_ms"), msg.get("w"), msg.get("h")
        if not (
            isinstance(seq, int)
            and not isinstance(seq, bool)
            and _is_number(t_phone)
            and isinstance(w, int)
            and isinstance(h, int)
        ):
            await conn.close(4000, "bad_frame_meta")
            return False
        conn.pending_meta = {"seq": seq, "t_phone_ms": float(t_phone), "w": w, "h": h}
        return True

    if kind == "ping":
        t_phone = msg.get("t_phone_ms")
        if not _is_number(t_phone):
            await conn.close(4000, "bad_ping")
            return False
        t_server = now_ms()
        last_rtt = msg.get("last_rtt_ms")
        if _is_number(last_rtt) and last_rtt >= 0:
            conn.offset.update(float(t_phone), float(last_rtt), t_server)
        await conn.send_json({"type": "pong", "t_phone_ms": t_phone, "t_server_ms": t_server})
        if conn.status == "offline":
            await hub.set_status(conn.camera_id, "online", "ping")
        return True

    if kind == "pause":
        await hub.set_status(conn.camera_id, "paused", "phone_hidden")
        return True

    if kind == "resume":
        await hub.set_status(conn.camera_id, "online", "phone_visible")
        return True

    await conn.close(4000, "unknown_type")
    return False


async def _handle_binary(
    conn: CameraConn, data: bytes, hub: Hub, max_bytes: int, ack_every: int
) -> bool:
    meta = conn.pending_meta
    if meta is None:
        await conn.close(4000, "binary_without_meta")
        return False
    conn.pending_meta = None
    if len(data) > max_bytes:
        await conn.close(1009, "frame_too_large")
        return False
    conn.touch()
    conn.frames_in += 1
    conn.bytes_in += len(data)
    seq = int(meta["seq"])
    if seq <= conn.last_seq:
        conn.out_of_order += 1
    else:
        conn.last_seq = seq
    t_recv = now_ms()
    if conn.status == "offline":
        await hub.set_status(conn.camera_id, "online", "frame")
    ingest = getattr(hub, "ingest", None)  # installed by the pipeline layer (M3)
    if ingest is not None:
        ingest(conn, meta, data, t_recv)
    if conn.frames_in % ack_every == 0:
        await conn.send_json(
            {
                "type": "ack",
                "seq": seq,
                "t_server_recv_ms": t_recv,
                "processed_seq": None,
                "dropped_replaced": 0,
                "dropped_ratelimit": 0,
            }
        )
    return True
