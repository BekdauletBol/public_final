"""``/ws/dashboard``: admin fan-out socket (F.8)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session, sessionmaker

from publicsafe.api.auth import COOKIE_NAME, AdminAuth
from publicsafe.db.base import session_scope
from publicsafe.ws.hub import DashboardConn, Hub

log = logging.getLogger("publicsafe.ws.dashboard")
router = APIRouter()


def _is_admin(factory: sessionmaker[Session], auth: AdminAuth, token: str) -> bool:
    if not token:
        return False
    with session_scope(factory) as db:
        return auth.resolve(db, token) is not None


async def _sender(conn: DashboardConn) -> None:
    try:
        while True:
            message = await conn.queue.get()
            if isinstance(message, bytes):
                await conn.ws.send_bytes(message)
            else:
                await conn.ws.send_json(message)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    except Exception:
        log.exception("dashboard sender failed")


@router.websocket("/ws/dashboard")
async def dashboard_socket(ws: WebSocket) -> None:
    # Accept first so the browser can read the 4001 close code (F.8).
    await ws.accept()
    app = ws.app
    hub: Hub = app.state.hub
    token = ws.cookies.get(COOKIE_NAME, "")
    if not await asyncio.to_thread(_is_admin, app.state.sessionmaker, app.state.admin_auth, token):
        try:
            await ws.send_json(
                {"type": "error", "code": "unauthenticated", "message": "Admin login required"}
            )
            await ws.close(code=4001, reason="unauthenticated")
        except Exception:
            pass
        return

    conn = DashboardConn(ws)
    hub.add_dashboard(conn)
    sender = asyncio.create_task(_sender(conn))
    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg: Any = json.loads(raw)
            except ValueError:
                conn.offer({"type": "error", "code": "bad_json", "message": "Invalid JSON"})
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
                conn.offer({"type": "error", "code": "bad_message", "message": "Missing type"})
                continue
            await _handle(hub, conn, msg)
    finally:
        hub.remove_dashboard(conn)
        sender.cancel()


async def _handle(hub: Hub, conn: DashboardConn, msg: dict[str, Any]) -> None:
    kind = msg["type"]
    if kind == "subscribe":
        ids = msg.get("camera_ids", "all")
        if ids == "all":
            conn.camera_ids = None
        elif isinstance(ids, list) and all(isinstance(i, str) for i in ids):
            conn.camera_ids = set(ids)
        else:
            conn.offer({"type": "error", "code": "bad_subscribe", "message": "camera_ids"})
            return
        fps = msg.get("preview_fps", 10)
        if isinstance(fps, int) and not isinstance(fps, bool) and 1 <= fps <= 30:
            conn.preview_fps = fps
        for status in await hub.status_snapshot(conn.camera_ids):
            conn.offer(status)
        conn.offer({"type": "metrics", **hub.metrics_payload()})
        return
    if kind == "unsubscribe":
        ids = msg.get("camera_ids")
        if ids == "all":
            conn.camera_ids = set()
        elif isinstance(ids, list) and conn.camera_ids is not None:
            conn.camera_ids.difference_update(str(i) for i in ids)
        return
    conn.offer({"type": "error", "code": "unknown_type", "message": f"Unknown type {kind}"})
