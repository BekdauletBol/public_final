"""M2: /ws/camera auth, status transitions, close codes, ping/pong and the sweeper (F.7, G.3)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from publicsafe.core.timing import now_ms
from tests.conftest import tc_camera, tc_mint, tc_session
from tests.integration.fake_phone import TINY_JPEG, FakePhone


def wait_status(tc: TestClient, camera_id: str, expected: str, timeout: float = 3.0) -> str:
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = tc.get(f"/api/cameras/{camera_id}").json()["status"]
        if status == expected:
            return status
        time.sleep(0.05)
    return status or "unknown"


def paired(tc: TestClient) -> tuple[str, str]:
    s = tc_session(tc)
    m = tc_mint(tc, s["id"], camera_name_hint="Gate")
    r = tc.post("/api/pair/redeem", json={"pin": m["pin"], "code_id": m["code_id"]})
    assert r.status_code == 200, r.text
    return r.json()["camera_id"], r.json()["device_token"]


def test_hello_online_ping_pause_resume_offline(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    with FakePhone(tc, token) as phone:
        hello = phone.auth()
        assert hello["camera_id"] == camera_id and hello["camera_name"] == "Gate"
        assert hello["ack_every"] == 10 and hello["settings"]["target_fps"] == 10
        assert abs(hello["server_time_ms"] - now_ms()) < 5000
        assert wait_status(tc, camera_id, "online") == "online"
        assert tc.get(f"/api/cameras/{camera_id}").json()["last_seen_at"] is not None

        pong = phone.ping(last_rtt_ms=None, t_phone_ms=123.0)
        assert pong["t_phone_ms"] == 123.0 and pong["t_server_ms"] > 0

        phone.pause()
        assert wait_status(tc, camera_id, "paused") == "paused"
        phone.resume()
        assert wait_status(tc, camera_id, "online") == "online"
    assert wait_status(tc, camera_id, "offline") == "offline"


def test_clock_offset_estimate_with_40ms_skew(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        phone.ping(last_rtt_ms=None, t_phone_ms=now_ms() - 40)
        for _ in range(4):
            phone.ping(last_rtt_ms=0, t_phone_ms=now_ms() - 40)
        m = tc.get("/api/metrics").json()
        cam = m["cameras"][camera_id]
        assert 25 < cam["clock_offset_ms"] < 60, cam  # true skew 40 ms, loop jitter allowed
        assert cam["rtt_ms"] == 0 and cam["clock_offset_uncertainty_ms"] == 0
        assert m["server"]["active_cameras"] == 1


def test_auth_timeout_4001(make_tc) -> None:
    tc = make_tc(ws_auth_timeout_s=0.3)
    with FakePhone(tc, "unused") as phone:
        code, err = phone.expect_close()
    assert code == 4001 and err["code"] == "auth_timeout"


def test_bad_token_and_malformed_auth_4001(tc: TestClient) -> None:
    with FakePhone(tc, "not-a-token") as phone:
        phone.send({"type": "auth", "token": phone.token, "client": {}})
        code, err = phone.expect_close()
    assert code == 4001 and err["code"] == "unknown_token"

    with FakePhone(tc, "x") as phone:
        phone.send({"type": "frame_meta", "seq": 1})
        code, err = phone.expect_close()
    assert code == 4001 and err["code"] == "auth_invalid"

    with FakePhone(tc, "x") as phone:
        phone.ws.send_text("{not json")
        code, _ = phone.expect_close()
    assert code == 4001

    kinds = [
        e["details"]["reason"]
        for e in tc.get("/api/security/log").json()
        if e["kind"] == "ws_auth_failed"
    ]
    assert kinds.count("unknown_token") == 1 and kinds.count("malformed_auth") == 2


def test_revoked_4003_before_and_during_connection(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        wait_status(tc, camera_id, "online")
        t0 = time.monotonic()
        assert (
            tc.post(f"/api/cameras/{camera_id}/revoke", json={"reason": "test"}).status_code == 204
        )
        code, err = phone.expect_close()
        assert code == 4003 and err["code"] == "revoked"
        assert time.monotonic() - t0 < 1.0
    assert tc.get(f"/api/cameras/{camera_id}").json()["status"] == "revoked"

    with FakePhone(tc, token) as phone:
        phone.send({"type": "auth", "token": token, "client": {}})
        code, err = phone.expect_close()
    assert code == 4003 and err["code"] == "revoked"
    assert tc.get(f"/api/cameras/{camera_id}").json()["status"] == "revoked"  # never overwritten


def test_archived_session_4003_and_deleted_session_closes(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    session_id = tc.get(f"/api/cameras/{camera_id}").json()["session_id"]
    tc.patch(f"/api/sessions/{session_id}", json={"archived": True})
    with FakePhone(tc, token) as phone:
        phone.send({"type": "auth", "token": token, "client": {}})
        code, err = phone.expect_close()
    assert code == 4003 and err["code"] == "session_archived"
    tc.patch(f"/api/sessions/{session_id}", json={"archived": False})

    with FakePhone(tc, token) as phone:
        phone.auth()
        wait_status(tc, camera_id, "online")
        assert tc.delete(f"/api/sessions/{session_id}").status_code == 204
        code, err = phone.expect_close()
    assert code == 4003 and err["code"] == "session_deleted"


def test_superseded_4004(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    with FakePhone(tc, token) as first, FakePhone(tc, token) as second:
        first.auth()
        second.auth()
        code, err = first.expect_close()
        assert code == 4004 and err["code"] == "superseded"
        assert second.ping()["type"] == "pong"
        assert wait_status(tc, camera_id, "online") == "online"
    assert wait_status(tc, camera_id, "offline") == "offline"
    assert any(e["kind"] == "ws_superseded" for e in tc.get("/api/security/log").json())


def test_frames_acks_ordering_and_protocol_errors(make_tc) -> None:
    tc = make_tc(max_frame_bytes=10_000)
    camera_id, token = paired(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        for _ in range(10):
            phone.send_frame()
        ack = phone.recv()
        assert ack["type"] == "ack" and ack["seq"] == 10
        assert ack["dropped_replaced"] == 0 and ack["processed_seq"] is None
        phone.send_frame(seq=3)  # non-increasing: counted, not closed
        for _ in range(9):
            phone.send_frame()
        assert phone.recv()["seq"] == 19  # explicit seq=3 did not advance the counter
        m = tc.get("/api/metrics").json()["cameras"][camera_id]
        assert (
            m["frames_in"] == 20 and m["out_of_order"] == 1 and m["bytes_in"] == 20 * len(TINY_JPEG)
        )

    with FakePhone(tc, token) as phone:
        phone.auth()
        phone.ws.send_bytes(TINY_JPEG)  # binary without meta
        code, err = phone.expect_close()
        assert code == 4000 and err["code"] == "binary_without_meta"

    with FakePhone(tc, token) as phone:
        phone.auth()
        phone.send({"type": "teleport"})
        code, err = phone.expect_close()
        assert code == 4000 and err["code"] == "unknown_type"

    with FakePhone(tc, token) as phone:
        phone.auth()
        phone.ws.send_text("nope")
        code, _ = phone.expect_close()
        assert code == 4000

    with FakePhone(tc, token) as phone:
        phone.auth()
        phone.send({"type": "frame_meta", "seq": "1", "t_phone_ms": 1, "w": 1, "h": 1})
        code, err = phone.expect_close()
        assert code == 4000 and err["code"] == "bad_frame_meta"

    with FakePhone(tc, token) as phone:
        phone.auth()
        phone.send_frame(data=b"x" * 10_001)
        code, err = phone.expect_close()
        assert code == 1009 and err["code"] == "frame_too_large"


def test_sweeper_marks_offline_and_ping_restores(make_tc) -> None:
    tc = make_tc(offline_after_s=0.3, sweep_interval_s=0.1)
    camera_id, token = paired(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        assert wait_status(tc, camera_id, "online") == "online"
        assert wait_status(tc, camera_id, "offline", timeout=3) == "offline"
        phone.ping()
        assert wait_status(tc, camera_id, "online") == "online"


def test_settings_pushed_to_phone(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        wait_status(tc, camera_id, "online")
        r = tc.patch(f"/api/cameras/{camera_id}", json={"settings": {"target_fps": 4}})
        assert r.status_code == 200
        msg = phone.recv()
        assert msg["type"] == "settings" and msg["settings"]["target_fps"] == 4
        assert msg["settings"]["jpeg_quality"] == 65


def test_startup_marks_stale_online_cameras_offline(make_settings) -> None:
    from publicsafe.db.base import session_scope
    from publicsafe.db.models import Camera
    from publicsafe.main import create_app

    settings = make_settings()
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as tc:
        tc.headers.update({"X-Requested-With": "publicsafe"})
        tc.post("/api/auth/login", json={"username": "admin", "password": "correct horse battery"})
        s = tc_session(tc)
        cam = tc_camera(tc, s["id"])
    with session_scope(app.state.sessionmaker) as db:
        db.get(Camera, cam["camera_id"]).status = "online"
    with TestClient(create_app(settings), base_url="https://testserver") as tc2:
        tc2.headers.update({"X-Requested-With": "publicsafe"})
        tc2.post("/api/auth/login", json={"username": "admin", "password": "correct horse battery"})
        assert tc2.get(f"/api/cameras/{cam['camera_id']}").json()["status"] == "offline"
