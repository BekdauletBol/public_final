"""Scripted phone client for the camera WebSocket, built on Starlette's TestClient."""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from publicsafe.core.timing import now_ms

#: Smallest payload the pass-through accepts as "a frame" in M2 (decode happens in M3).
TINY_JPEG = bytes.fromhex("ffd8ffd9")


class FakePhone:
    """``with FakePhone(tc, token) as phone: hello = phone.auth()`` ..."""

    def __init__(self, tc: TestClient, token: str, client: dict[str, Any] | None = None) -> None:
        self.tc = tc
        self.token = token
        self.client = client or {"ua": "fake-phone/1", "w": 640, "h": 480, "remember": False}
        self.ws: WebSocketTestSession | None = None
        self.seq = 0
        self.hello: dict[str, Any] | None = None

    def __enter__(self) -> FakePhone:
        self._cm = self.tc.websocket_connect("/ws/camera")
        self.ws = self._cm.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(Exception):
            self._cm.__exit__(None, None, None)

    # --- protocol ------------------------------------------------------------

    def send(self, message: dict[str, Any]) -> None:
        assert self.ws is not None
        self.ws.send_json(message)

    def recv(self) -> dict[str, Any]:
        assert self.ws is not None
        return self.ws.receive_json()

    def auth(self) -> dict[str, Any]:
        self.send({"type": "auth", "token": self.token, "client": self.client})
        self.hello = self.recv()
        assert self.hello["type"] == "hello", self.hello
        return self.hello

    def ping(self, last_rtt_ms: float | None = None, t_phone_ms: float | None = None) -> dict:
        self.send(
            {
                "type": "ping",
                "t_phone_ms": now_ms() if t_phone_ms is None else t_phone_ms,
                "last_rtt_ms": last_rtt_ms,
            }
        )
        pong = self.recv()
        assert pong["type"] == "pong", pong
        return pong

    def send_frame(
        self, data: bytes = TINY_JPEG, w: int = 640, h: int = 480, seq: int | None = None
    ) -> int:
        assert self.ws is not None
        if seq is None:
            self.seq += 1
            seq = self.seq
        self.send({"type": "frame_meta", "seq": seq, "t_phone_ms": now_ms(), "w": w, "h": h})
        self.ws.send_bytes(data)
        return seq

    def pause(self) -> None:
        self.send({"type": "pause"})

    def resume(self) -> None:
        self.send({"type": "resume"})

    def expect_close(self) -> tuple[int, dict[str, Any] | None]:
        """Read until the server closes; returns (close code, last error message if any)."""
        assert self.ws is not None
        last_error: dict[str, Any] | None = None
        try:
            while True:
                message = self.ws.receive_json()
                if message.get("type") == "error":
                    last_error = message
        except WebSocketDisconnect as exc:
            return exc.code, last_error
