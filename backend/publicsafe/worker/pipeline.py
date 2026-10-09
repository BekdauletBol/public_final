"""Per-frame processing: decode -> (detect) -> publish (H.1). Rules and events arrive in M4."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

from publicsafe.core.detector import Detector, NullDetector
from publicsafe.core.models import Frame, FrameResult, Timings
from publicsafe.core.timing import now_ms

log = logging.getLogger("publicsafe.pipeline")


class DecodeError(ValueError):
    """The JPEG could not be decoded."""


def decode_jpeg(data: bytes, max_long_edge: int | None = None) -> np.ndarray:
    """Decode to BGR; resize only if the long edge exceeds ``max_long_edge``."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise DecodeError("cv2.imdecode returned None")
    if max_long_edge:
        h, w = image.shape[:2]
        long_edge = max(h, w)
        if long_edge > max_long_edge:
            scale = max_long_edge / long_edge
            image = cv2.resize(
                image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA
            )
    return image


Publisher = Callable[[FrameResult, bytes], None]
SettingsLookup = Callable[[str], dict[str, Any]]


class Pipeline:
    """Process one frame at a time on the worker thread."""

    def __init__(
        self,
        publish: Publisher,
        settings_for: SettingsLookup,
        detector: Detector | None = None,
    ) -> None:
        self.publish = publish
        self.settings_for = settings_for
        self.detector: Detector = detector or NullDetector()

    def process(self, frame: Frame) -> FrameResult:
        t_start = now_ms()
        if frame.t_dequeued_ms is None:
            frame.t_dequeued_ms = t_start
        settings = self.settings_for(frame.camera_id)
        image = decode_jpeg(frame.jpeg, int(settings.get("max_long_edge") or 0) or None)
        t_decoded = now_ms()
        h, w = image.shape[:2]
        frame.image = image
        frame.w, frame.h = w, h  # the decoded size is the truth (H.1)

        detections = self.detector.detect(image)
        t_inferred = now_ms()

        timings = Timings(
            queue_wait_ms=frame.t_dequeued_ms - frame.t_server_recv_ms,
            decode_ms=t_decoded - t_start,
            infer_ms=t_inferred - t_decoded,
            rules_ms=0.0,
        )
        result = FrameResult(
            camera_id=frame.camera_id,
            seq=frame.seq,
            t_server_recv_ms=frame.t_server_recv_ms,
            t_processed_ms=t_inferred,
            w=w,
            h=h,
            t_phone_ms=frame.t_phone_ms,
            detections=detections,
            timings=timings,
        )
        self.publish(result, frame.jpeg)
        t_published = now_ms()
        timings.publish_ms = t_published - t_inferred
        timings.total_ms = t_published - frame.t_server_recv_ms
        frame.image = None  # release pixels as soon as possible (F-PRIV-1)
        return result
