"""M2: /ws/dashboard auth, subscribe/unsubscribe, camera_status and metrics (F.8)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.conftest import tc_mint, tc_session
from tests.integration.fake_phone import FakePhone


def admin_ws(tc: TestClient):
    """The test client upgrades over ws:// so the Secure cookie must be passed by hand."""
    cookie = tc.cookies.get("ps_admin")
    assert cookie
    return tc.websocket_connect("/ws/dashboard", headers={"cookie": f"ps_admin={cookie}"})


def drain_until(ws, predicate, limit: int = 50) -> dict:
    for _ in range(limit):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    raise AssertionError("message not received")


def test_requires_admin_cookie_close_4001_after_accept(tc: TestClient) -> None:
    anon = TestClient(tc.app, base_url="https://testserver")
    with anon.websocket_connect("/ws/dashboard") as ws:
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "unauthenticated"
        try:
            ws.receive_json()
            raise AssertionError("expected close")
        except WebSocketDisconnect as exc:
            assert exc.code == 4001


def test_subscribe_snapshot_status_updates_and_metrics(tc: TestClient) -> None:
    s = tc_session(tc)
    m = tc_mint(tc, s["id"], camera_name_hint="Gate")
    r = tc.post("/api/pair/redeem", json={"pin": m["pin"], "code_id": m["code_id"]})
    camera_id, token = r.json()["camera_id"], r.json()["device_token"]

    with admin_ws(tc) as ws:
        ws.send_json({"type": "subscribe", "camera_ids": "all", "preview_fps": 5})
        snap = drain_until(ws, lambda m: m["type"] == "camera_status")
        assert snap["camera_id"] == camera_id and snap["status"] == "paired"
        assert snap["reason"] == "snapshot" and snap["session_id"] == s["id"]
        metrics = drain_until(ws, lambda m: m["type"] == "metrics")
        assert "server" in metrics and "cameras" in metrics
        assert metrics["server"]["active_cameras"] == 0

        with FakePhone(tc, token) as phone:
            phone.auth()
            online = drain_until(
                ws, lambda m: m["type"] == "camera_status" and m["status"] == "online"
            )
            assert online["camera_id"] == camera_id and online["reason"] == "connected"
            assert online["last_seen_at"].endswith("Z")
            phone.pause()
            paused = drain_until(
                ws, lambda m: m["type"] == "camera_status" and m["status"] == "paused"
            )
            assert paused["reason"] == "phone_hidden"
        offline = drain_until(
            ws, lambda m: m["type"] == "camera_status" and m["status"] == "offline"
        )
        assert offline["reason"] == "disconnected"

        ws.send_json({"type": "bogus"})
        err = drain_until(ws, lambda m: m["type"] == "error")
        assert err["code"] == "unknown_type"
        ws.send_text("{bad")
        assert drain_until(ws, lambda m: m["type"] == "error")["code"] == "bad_json"


def test_subscription_filter(tc: TestClient) -> None:
    s = tc_session(tc)
    cams = []
    for name in ("A", "B"):
        m = tc_mint(tc, s["id"], camera_name_hint=name)
        r = tc.post("/api/pair/redeem", json={"pin": m["pin"], "code_id": m["code_id"]})
        cams.append((r.json()["camera_id"], r.json()["device_token"]))
    (a_id, a_token), (b_id, b_token) = cams

    with admin_ws(tc) as ws:
        ws.send_json({"type": "subscribe", "camera_ids": [b_id], "preview_fps": 10})
        snap = drain_until(ws, lambda m: m["type"] == "camera_status")
        assert snap["camera_id"] == b_id
        drain_until(ws, lambda m: m["type"] == "metrics")
        with FakePhone(tc, a_token) as pa, FakePhone(tc, b_token) as pb:
            pa.auth()
            pb.auth()
            first = drain_until(ws, lambda m: m["type"] == "camera_status")
            assert first["camera_id"] == b_id and first["status"] == "online"
        ws.send_json({"type": "unsubscribe", "camera_ids": [b_id]})
        ws.send_json({"type": "subscribe", "camera_ids": "all"})
        statuses = {}
        for _ in range(2):
            msg = drain_until(ws, lambda m: m["type"] == "camera_status")
            statuses[msg["camera_id"]] = msg["status"]
        assert set(statuses) == {a_id, b_id}
