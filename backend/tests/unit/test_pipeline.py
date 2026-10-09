"""M3: decode -> (fake) detect -> publish, and the worker thread's error isolation."""

from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from publicsafe.core.detector import FakeDetector, NullDetector
from publicsafe.core.mailbox import Scheduler
from publicsafe.core.models import Frame, FrameResult
from publicsafe.worker.inference import InferenceWorker
from publicsafe.worker.pipeline import DecodeError, Pipeline, decode_jpeg


def jpeg(w: int = 64, h: int = 48) -> bytes:
    image = np.zeros((h, w, 3), np.uint8)
    image[:, :, 1] = 200
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 70])
    assert ok
    return buf.tobytes()


def make_frame(data: bytes, seq: int = 1, camera: str = "cam") -> Frame:
    return Frame(camera, seq, t_phone_ms=1000.0, t_server_recv_ms=1010.0, w=1, h=1, jpeg=data)


def test_decode_and_resize() -> None:
    image = decode_jpeg(jpeg(640, 480))
    assert image.shape == (480, 640, 3)
    small = decode_jpeg(jpeg(640, 480), max_long_edge=320)
    assert small.shape == (240, 320, 3)
    assert decode_jpeg(jpeg(64, 48), max_long_edge=320).shape == (48, 64, 3)  # no upscale
    with pytest.raises(DecodeError):
        decode_jpeg(b"not a jpeg")


def test_pipeline_publishes_result_with_fake_detections() -> None:
    published: list[tuple[FrameResult, bytes]] = []
    detector = FakeDetector([[{"bbox": [10, 10, 30, 40], "conf": 0.8}], []])
    p = Pipeline(
        lambda r, j: published.append((r, j)), lambda _cid: {"max_long_edge": 1280}, detector
    )
    data = jpeg()
    r1 = p.process(make_frame(data, seq=1))
    r2 = p.process(make_frame(data, seq=2))
    assert len(published) == 2 and published[0][1] is data
    assert (r1.w, r1.h) == (64, 48)
    assert len(r1.detections) == 1 and r1.detections[0].anchors["bc"] == (20.0, 40.0)
    assert r2.detections == []
    assert r1.timings.total_ms >= 0 and r1.timings.decode_ms >= 0
    js = r1.to_json()
    assert js["type"] == "frame_result" and js["detections"][0]["bbox"] == [10.0, 10.0, 30.0, 40.0]
    assert set(js["timings"]) == {"queue_wait_ms", "decode_ms", "infer_ms", "rules_ms", "total_ms"}


def test_pipeline_uses_decoded_size_as_truth() -> None:
    p = Pipeline(lambda r, j: None, lambda _cid: {}, NullDetector())
    frame = make_frame(jpeg(32, 16))
    frame.w, frame.h = 999, 999  # phone lied in frame_meta
    result = p.process(frame)
    assert (result.w, result.h) == (32, 16) and (frame.w, frame.h) == (32, 16)
    assert frame.image is None  # pixels released after publish


def test_worker_processes_and_survives_bad_frames() -> None:
    scheduler = Scheduler()
    results: list[FrameResult] = []
    errors: list[tuple[int, str]] = []
    pipeline = Pipeline(lambda r, j: results.append(r), lambda _cid: {}, FakeDetector(sleep_ms=1))
    worker = InferenceWorker(
        scheduler,
        pipeline,
        on_result=lambda f, r: None,
        on_error=lambda f, exc: errors.append((f.seq, type(exc).__name__)),
        poll_s=0.05,
    )
    worker.start()
    data = jpeg()
    for seq in range(1, 4):
        scheduler.put(make_frame(data, seq=seq, camera=f"cam{seq}"))
    scheduler.put(make_frame(b"garbage", seq=9, camera="bad"))
    scheduler.put(make_frame(data, seq=5, camera="cam5"))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and worker.processed + worker.errors < 5:
        time.sleep(0.02)
    worker.stop()
    assert worker.processed == 4 and worker.errors == 1
    assert errors == [(9, "DecodeError")]
    assert sorted(r.camera_id for r in results) == ["cam1", "cam2", "cam3", "cam5"]
    assert all(r.timings.queue_wait_ms >= 0 for r in results)
