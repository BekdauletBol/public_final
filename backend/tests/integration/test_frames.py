"""M3: frame ingest -> scheduler -> worker -> dashboard fan-out, limits, metrics (E.3, F.8)."""

from __future__ import annotations

import contextlib
import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from publicsafe.db.base import session_scope
from publicsafe.db.models import Camera, MetricSample
from tests.conftest import tc_mint, tc_session
from tests.integration.fake_phone import FakePhone
from tests.integration.test_ws_dashboard import admin_ws, drain_until


def jpeg(w: int = 96, h: int = 64) -> bytes:
    image = np.full((h, w, 3), 120, np.uint8)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 60])
    assert ok
    return buf.tobytes()


def paired(tc: TestClient, name: str = "Gate") -> tuple[str, str]:
    s = tc_session(tc)
    m = tc_mint(tc, s["id"], camera_name_hint=name)
    r = tc.post("/api/pair/redeem", json={"pin": m["pin"], "code_id": m["code_id"]})
    assert r.status_code == 200, r.text
    return r.json()["camera_id"], r.json()["device_token"]


def wait_for(predicate, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_frames_reach_dashboard_with_preview_and_privacy_mode(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    data = jpeg()
    with admin_ws(tc) as ws, FakePhone(tc, token) as phone:
        ws.send_json({"type": "subscribe", "camera_ids": "all", "preview_fps": 30})
        phone.auth()
        drain_until(ws, lambda m: m["type"] == "camera_status" and m["status"] == "online")
        phone.send_frame(data, w=96, h=64)
        result = drain_until(ws, lambda m: m["type"] == "frame_result")
        assert result["camera_id"] == camera_id and result["seq"] == 1
        assert (result["w"], result["h"]) == (96, 64)
        assert result["detections"] == [] and result["zones"] == []
        assert result["timings"]["total_ms"] >= 0
        preview = ws.receive_json()
        assert preview["type"] == "frame_preview" and preview["bytes"] == len(data)
        assert ws.receive_bytes() == data

        # privacy mode: results keep flowing, binary stops
        r = tc.patch(f"/api/cameras/{camera_id}", json={"settings": {"privacy_mode": True}})
        assert r.status_code == 200
        assert phone.recv()["type"] == "settings"
        phone.send_frame(data)
        drain_until(ws, lambda m: m["type"] == "frame_result" and m["seq"] == 2)
        phone.ping()  # anything after it must be JSON, not a preview
        nxt = drain_until(ws, lambda m: m["type"] in ("frame_preview", "metrics", "camera_status"))
        assert nxt["type"] != "frame_preview"
        r = tc.get(f"/api/cameras/{camera_id}/last-frame.jpg")
        assert r.status_code == 404 and r.json()["error"]["code"] == "privacy_mode"

        tc.patch(f"/api/cameras/{camera_id}", json={"settings": {"privacy_mode": False}})
        r = tc.get(f"/api/cameras/{camera_id}/last-frame.jpg")
        assert r.status_code == 200 and r.content == data
        assert r.headers["cache-control"] == "no-store"


def test_preview_throttled_to_subscriber_fps(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    data = jpeg()
    with admin_ws(tc) as ws, FakePhone(tc, token) as phone:
        ws.send_json({"type": "subscribe", "camera_ids": [camera_id], "preview_fps": 2})
        phone.auth()
        for _ in range(10):
            phone.send_frame(data)
            time.sleep(0.03)
        results, previews = 0, 0
        deadline = time.monotonic() + 3
        while results < 10 and time.monotonic() < deadline:
            msg = ws.receive_json()
            if msg["type"] == "frame_result":
                results += 1
            elif msg["type"] == "frame_preview":
                previews += 1
                ws.receive_bytes()
        assert results == 10
        assert 1 <= previews <= 2  # ~0.3 s of frames at 2 preview fps


def test_acks_carry_processed_seq_and_metrics_have_percentiles(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    data = jpeg()
    with FakePhone(tc, token) as phone:
        phone.auth()
        for _ in range(10):
            phone.send_frame(data)
            time.sleep(0.01)
        ack = phone.recv()
        assert ack["type"] == "ack" and ack["seq"] == 10
        assert ack["dropped_ratelimit"] == 0
        m = wait_for(
            lambda: (lambda p: p if p["cameras"][camera_id]["processed"] == 10 else None)(
                tc.get("/api/metrics").json()
            )
        )
        cam = m["cameras"][camera_id]
        assert cam["frames_in"] == 10 and cam["fps_in"] > 0 and cam["fps_processed"] > 0
        assert (
            cam["spans"]["total"]["p50"] is not None
            and cam["spans"]["total"]["p99"] >= cam["spans"]["total"]["p50"]
        )
        assert cam["spans"]["queue_wait"]["p50"] >= 0
        assert cam["processed_seq"] == 10 and cam["last_w"] == 96
        assert (
            m["server"]["scheduler_strategy"] == "latest" and m["server"]["scheduler_backlog"] == 0
        )
        summary = tc.get("/api/cameras").json()[0]
        assert summary["fps_in"] > 0 and summary["drop_ratio"] == 0.0
        detail = tc.get(f"/api/cameras/{camera_id}").json()
        assert detail["health"]["processed"] == 10


def test_rate_limit_drops_above_2x_target_and_closes_on_abuse(make_tc) -> None:
    tc = make_tc(rate_abuse_seconds=0.6)
    camera_id, token = paired(tc)
    tc.patch(f"/api/cameras/{camera_id}", json={"settings": {"target_fps": 2}})
    data = jpeg()
    with FakePhone(tc, token) as phone:
        phone.auth()
        for _ in range(10):  # burst well above 2x2 fps in < 1 s
            phone.send_frame(data)
        ack = phone.recv()
        assert ack["type"] == "ack" and ack["dropped_ratelimit"] >= 5
        time.sleep(0.7)
        code, err = None, None
        for _ in range(60):  # > 3x target sustained beyond rate_abuse_seconds
            try:
                phone.send_frame(data)
            except Exception:  # the server closed the socket
                break
            time.sleep(0.02)
        with contextlib.suppress(Exception):
            code, err = phone.expect_close()
        assert code == 4008 and err["code"] == "rate_abuse"
    cam = tc.get("/api/metrics").json()["cameras"][camera_id]
    assert cam["dropped_ratelimit"] >= 5 and cam["drop_ratio"] > 0


def test_decode_errors_counted_and_worker_errors_mark_camera(tc: TestClient) -> None:
    camera_id, token = paired(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        phone.send_frame(b"\xff\xd8garbage\xff\xd9")
        phone.send_frame(jpeg())
        m = wait_for(
            lambda: (lambda p: p if p["cameras"][camera_id]["processed"] == 1 else None)(
                tc.get("/api/metrics").json()
            )
        )
        assert m["cameras"][camera_id]["decode_errors"] == 1
        assert tc.get(f"/api/cameras/{camera_id}").json()["status"] == "online"

        # a genuine worker exception marks the camera `error`, next success restores `online`
        pipeline = tc.app.state.pipeline
        original = pipeline.detector

        class Boom:
            name = "boom"

            def detect(self, image):
                raise RuntimeError("model exploded")

        pipeline.detector = Boom()
        phone.send_frame(jpeg())
        wait_for(lambda: tc.get(f"/api/cameras/{camera_id}").json()["status"] == "error")
        pipeline.detector = original
        phone.send_frame(jpeg())
        wait_for(lambda: tc.get(f"/api/cameras/{camera_id}").json()["status"] == "online")
        assert tc.get("/api/metrics").json()["cameras"][camera_id]["worker_errors"] == 1


def test_metric_samples_written_and_exported(make_tc) -> None:
    tc = make_tc(metric_sample_interval_s=0.2)
    camera_id, token = paired(tc)
    with FakePhone(tc, token) as phone:
        phone.auth()
        for _ in range(5):
            phone.send_frame(jpeg(80, 40))
        time.sleep(0.6)
    with session_scope(tc.app.state.sessionmaker) as db:
        rows = db.query(MetricSample).filter_by(camera_id=camera_id).all()
        assert rows, "no metric samples written"
        total_in = sum(r.fps_in * r.window_s for r in rows)
        assert 4.5 <= total_in <= 5.5
        assert any(r.total_p50 is not None for r in rows)
        assert all(r.server_mem_mb and r.server_mem_mb > 0 for r in rows)
        cam = db.get(Camera, camera_id)
        assert (cam.last_frame_w, cam.last_frame_h) == (80, 40)
    r = tc.get("/api/metrics/export.csv", params={"camera_id": camera_id})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,camera_id,camera_name,t,window_s,fps_in,fps_processed,")
    assert lines[0].endswith("server_cpu,server_mem_mb")
    assert len(lines) == len(rows) + 1 and "Gate" in lines[1]
    assert tc.get("/api/metrics/export.csv", params={"from": "junk"}).status_code == 400
    assert (
        tc.get("/api/metrics/export.csv", params={"to": "2000-01-01T00:00:00Z"}).text.count("\n")
        <= 1
    )


def test_fake_detector_script_endpoint(make_tc) -> None:
    tc = make_tc(detector="fake")
    camera_id, token = paired(tc)
    r = tc.post(
        "/api/test/detector", json={"script": [[{"bbox": [1, 2, 11, 22], "conf": 0.7}], []]}
    )
    assert r.status_code == 204
    with admin_ws(tc) as ws, FakePhone(tc, token) as phone:
        ws.send_json({"type": "subscribe", "camera_ids": [camera_id], "preview_fps": 1})
        phone.auth()
        phone.send_frame(jpeg())
        phone.send_frame(jpeg())
        first = drain_until(ws, lambda m: m["type"] == "frame_result" and m["seq"] == 1)
        second = drain_until(ws, lambda m: m["type"] == "frame_result" and m["seq"] == 2)
        assert first["detections"][0]["bbox"] == [1.0, 2.0, 11.0, 22.0]
        assert first["detections"][0]["anchors"]["bc"] == [6.0, 22.0]
        assert second["detections"] == []


def test_detector_endpoint_refused_without_fake_detector(tc: TestClient) -> None:
    r = tc.post("/api/test/detector", json={"script": []})
    assert r.status_code == 409 and r.json()["error"]["code"] == "detector_not_fake"
