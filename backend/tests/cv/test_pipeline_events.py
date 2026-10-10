"""M4: FakeDetector pipeline end to end; a scripted 60-frame sequence yields the expected events."""

from __future__ import annotations

import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from publicsafe.db.base import session_scope
from publicsafe.db.models import Event, Zone
from tests.conftest import tc_mint, tc_session
from tests.integration.fake_phone import FakePhone
from tests.integration.test_ws_dashboard import admin_ws, drain_until

W, H = 640, 360
IN_ZONE = {"bbox": [280, 150, 360, 330], "conf": 0.9}  # feet at y=330 inside the lower zone
OUT_ZONE = {"bbox": [20, 20, 90, 120], "conf": 0.8}  # top-left corner, outside
STRADDLE = {"bbox": [300, 100, 380, 358], "conf": 0.7}  # feet below the zone: bbox_overlap only
ZONE_POINTS = [[0.2, 0.6], [0.8, 0.6], [0.8, 0.95], [0.2, 0.95]]


def jpeg() -> bytes:
    ok, buf = cv2.imencode(
        ".jpg", np.full((H, W, 3), 100, np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 50]
    )
    assert ok
    return buf.tobytes()


def script_60_frames() -> list[list[dict]]:
    """10 empty, 20 with one person in the zone, 5 flicker, 15 empty, 10 with two in the zone."""
    frames: list[list[dict]] = []
    frames += [[] for _ in range(10)]
    frames += [[IN_ZONE] for _ in range(20)]
    frames += [[IN_ZONE], [], [IN_ZONE], [], [IN_ZONE]]
    frames += [[] for _ in range(15)]
    frames += [[IN_ZONE, {"bbox": [400, 160, 470, 335], "conf": 0.85}] for _ in range(10)]
    return frames


def paired_with_zone(tc: TestClient) -> tuple[str, str, str]:
    s = tc_session(tc)
    m = tc_mint(tc, s["id"], camera_name_hint="Gate")
    r = tc.post("/api/pair/redeem", json={"pin": m["pin"], "code_id": m["code_id"]})
    camera_id, token = r.json()["camera_id"], r.json()["device_token"]
    # 30 fps target so the 2x-target rate guard never drops the test's quick frames
    assert (
        tc.patch(f"/api/cameras/{camera_id}", json={"settings": {"target_fps": 30}}).status_code
        == 200
    )
    z = tc.post(
        f"/api/cameras/{camera_id}/zones",
        json={"name": "kerb", "points": ZONE_POINTS, "frame_w": W, "frame_h": H},
    )
    assert z.status_code == 201, z.text
    return camera_id, token, z.json()["id"]


def test_scripted_sequence_produces_expected_events(make_tc) -> None:
    tc = make_tc(detector="fake")
    camera_id, token, zone_id = paired_with_zone(tc)
    assert tc.post("/api/test/detector", json={"script": script_60_frames()}).status_code == 204
    data = jpeg()
    received: list[dict] = []
    with admin_ws(tc) as ws, FakePhone(tc, token) as phone:
        ws.send_json({"type": "subscribe", "camera_ids": [camera_id], "preview_fps": 1})
        phone.auth()
        # Lockstep: wait for each frame_result so the mailbox never replaces a frame and the
        # scripted detector advances exactly one entry per frame.
        for seq in range(1, 61):
            time.sleep(0.02)  # stay under 2x the 30 fps target (rate guard)
            phone.send_frame(data, w=W, h=H)
            while True:
                msg = drain_until(ws, lambda m: m["type"] in ("event", "frame_result"))
                if msg["type"] == "event":
                    received.append(msg["event"])
                elif msg["seq"] == seq:
                    break

    kinds = [(e["type"], e["count_before"], e["count_after"]) for e in received]
    assert kinds == [
        ("zone_entered", 0, 1),  # frame 13 (3 consecutive in-zone frames)
        ("zone_cleared", 1, 0),  # 5 consecutive empty frames after the flicker
        ("zone_entered", 0, 2),
    ]
    first = received[0]
    assert first["camera_id"] == camera_id and first["zone_id"] == zone_id
    assert first["zone_name"] == "kerb" and first["active_rule"] == "bottom_center"
    assert first["frame_seq"] == 13
    assert set(first["rule_counts"]) == {
        "bbox_overlap",
        "bottom_center",
        "three_point_any",
        "three_point_majority",
        "three_point_all",
        "bottom_strip",
    }
    assert first["rule_counts"]["bottom_center"] == 1 and first["t_server"].endswith("Z")

    with session_scope(tc.app.state.sessionmaker) as db:
        rows = db.query(Event).order_by(Event.t_server).all()
        assert [r.type for r in rows][:3] == ["zone_entered", "zone_cleared", "zone_entered"]
        assert rows[0].rule_counts["three_point_all"] == 1

    listed = tc.get("/api/events", params={"camera_id": camera_id}).json()
    assert [e["type"] for e in listed["items"]][::-1][:3] == [
        "zone_entered",
        "zone_cleared",
        "zone_entered",
    ]
    csv_text = tc.get("/api/events/export.csv", params={"zone_id": zone_id}).text
    header = csv_text.splitlines()[0]
    assert header.endswith(
        "active_rule,bbox_overlap,bottom_center,three_point_any,three_point_majority,"
        "three_point_all,bottom_strip"
    )
    assert "Gate" in csv_text.splitlines()[1] and "kerb" in csv_text.splitlines()[1]


def test_frame_result_zone_payload_and_rule_hits(make_tc) -> None:
    tc = make_tc(detector="fake")
    camera_id, token, zone_id = paired_with_zone(tc)
    tc.post("/api/test/detector", json={"script": [[IN_ZONE, OUT_ZONE, STRADDLE]]})
    with admin_ws(tc) as ws, FakePhone(tc, token) as phone:
        ws.send_json({"type": "subscribe", "camera_ids": [camera_id], "preview_fps": 1})
        phone.auth()
        phone.send_frame(jpeg(), w=W, h=H)
        result = drain_until(ws, lambda m: m["type"] == "frame_result")
    assert len(result["detections"]) == 3
    assert result["detections"][0]["anchors"]["bc"] == [320.0, 330.0]
    zone = result["zones"][0]
    assert zone["zone_id"] == zone_id and zone["state"] == "clear" and zone["debounced_count"] == 0
    assert zone["rule_hits"]["bottom_center"] == [0]
    assert zone["rule_hits"]["bbox_overlap"] == [0, 2]
    assert zone["counts"]["three_point_all"] == 1 and zone["counts"]["bbox_overlap"] == 2
    assert result["timings"]["rules_ms"] >= 0


def test_zone_changes_apply_live_and_disabled_freezes(make_tc) -> None:
    tc = make_tc(detector="fake")
    camera_id, token, zone_id = paired_with_zone(tc)
    tc.post("/api/test/detector", json={"script": [[IN_ZONE]]})
    tc.patch(f"/api/zones/{zone_id}", json={"enter_frames": 1, "exit_frames": 1})
    with admin_ws(tc) as ws, FakePhone(tc, token) as phone:
        ws.send_json({"type": "subscribe", "camera_ids": [camera_id], "preview_fps": 1})
        phone.auth()
        phone.send_frame(jpeg(), w=W, h=H)
        ev = drain_until(ws, lambda m: m["type"] == "event")["event"]
        assert ev["type"] == "zone_entered"
        # switching the rule keeps the state: no synthetic event
        tc.patch(f"/api/zones/{zone_id}", json={"active_rule": "bbox_overlap"})
        phone.send_frame(jpeg(), w=W, h=H)
        r = drain_until(ws, lambda m: m["type"] == "frame_result" and m["seq"] == 2)
        assert (
            r["zones"][0]["active_rule"] == "bbox_overlap" and r["zones"][0]["state"] == "occupied"
        )
        # disabled: frozen, no events even when empty
        tc.patch(f"/api/zones/{zone_id}", json={"enabled": False})
        tc.post("/api/test/detector", json={"script": [[]]})
        phone.send_frame(jpeg(), w=W, h=H)
        r = drain_until(ws, lambda m: m["type"] == "frame_result" and m["seq"] == 3)
        assert r["zones"][0]["enabled"] is False and r["zones"][0]["state"] == "occupied"
        # deleted zone disappears from results
        tc.delete(f"/api/zones/{zone_id}")
        phone.send_frame(jpeg(), w=W, h=H)
        r = drain_until(ws, lambda m: m["type"] == "frame_result" and m["seq"] == 4)
        assert r["zones"] == []
    assert len(tc.get("/api/events").json()["items"]) == 1


def test_needs_review_on_aspect_change(make_tc) -> None:
    tc = make_tc(detector="fake")
    camera_id, token, zone_id = paired_with_zone(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        _ok, buf = cv2.imencode(".jpg", np.zeros((640, 480, 3), np.uint8))  # portrait now
        phone.send_frame(buf.tobytes(), w=480, h=640)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if tc.get(f"/api/cameras/{camera_id}/zones").json()[0]["needs_review"]:
                break
            time.sleep(0.05)
    zone = tc.get(f"/api/cameras/{camera_id}/zones").json()[0]
    assert zone["needs_review"] is True
    assert tc.get("/api/cameras").json()[0]["needs_review"] is True
    r = tc.patch(
        f"/api/zones/{zone_id}", json={"points": ZONE_POINTS, "frame_w": 480, "frame_h": 640}
    )
    assert r.json()["needs_review"] is False
    with session_scope(tc.app.state.sessionmaker) as db:
        assert db.get(Zone, zone_id).needs_review is False


def test_eval_record_writes_frames_and_jsonl(make_tc, tmp_path) -> None:
    tc = make_tc(detector="fake", eval_record=True, data_dir=tmp_path / "data")
    camera_id, token, _ = paired_with_zone(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        for n in range(1, 4):
            phone.send_frame(jpeg(), w=W, h=H)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:  # lockstep so no frame is replaced
                if tc.get("/api/metrics").json()["cameras"][camera_id]["processed"] == n:
                    break
                time.sleep(0.02)
    rec = tmp_path / "data" / "recordings" / camera_id
    days = list(rec.iterdir())
    assert len(days) == 1
    files = sorted(p.name for p in days[0].iterdir())
    assert files == ["00000001.jpg", "00000002.jpg", "00000003.jpg", "frames.jsonl"]
    lines = (days[0] / "frames.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3 and '"zones"' in lines[0] and '"t_phone_ms"' in lines[0]
    assert tc.get("/api/metrics").json()["server"]["eval_record"] is True


def test_no_recording_by_default(make_tc, tmp_path) -> None:
    tc = make_tc(detector="fake", data_dir=tmp_path / "data")
    _camera_id, token, _ = paired_with_zone(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        phone.send_frame(jpeg(), w=W, h=H)
        time.sleep(0.3)
    assert not (tmp_path / "data" / "recordings").exists()
