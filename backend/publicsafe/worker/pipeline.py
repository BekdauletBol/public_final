"""Per-frame processing: decode -> (detect) -> publish (H.1). Rules and events arrive in M4."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

from publicsafe.core.detector import Detector, NullDetector
from publicsafe.core.models import Frame, FrameResult, Timings
from publicsafe.core.rules import compute_all_rules
from publicsafe.core.state_machine import ZoneEvent
from publicsafe.core.timing import now_ms
from publicsafe.worker.recorder import Recorder
from publicsafe.worker.zones import ZoneRegistry, ZoneRuntime

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
EventSink = Callable[[Frame, FrameResult, ZoneRuntime, ZoneEvent, dict[str, int]], None]


class Pipeline:
    """Process one frame at a time on the worker thread."""

    def __init__(
        self,
        publish: Publisher,
        settings_for: SettingsLookup,
        detector: Detector | None = None,
        zones: ZoneRegistry | None = None,
        on_event: EventSink | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        self.publish = publish
        self.settings_for = settings_for
        self.detector: Detector = detector or NullDetector()
        self.zones = zones
        self.on_event = on_event
        self.recorder = recorder

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

        conf = settings.get("confidence")
        detections = (
            self.detector.detect(image, conf=float(conf))  # type: ignore[call-arg]
            if conf is not None and hasattr(self.detector, "model_sha256")
            else self.detector.detect(image)
        )
        t_inferred = now_ms()

        zone_results: list[dict[str, Any]] = []
        pending_events: list[tuple[ZoneRuntime, ZoneEvent, dict[str, int]]] = []
        for zone in self.zones.for_camera(frame.camera_id) if self.zones else []:
            self.zones.check_aspect(zone, w, h) if self.zones else None
            hits, counts = compute_all_rules(detections, zone.polygon_for(w, h))
            raw = counts[zone.active_rule]
            counts_json = {rule.value: n for rule, n in counts.items()}
            for event in zone.machine.step(raw, frame.t_server_recv_ms):
                pending_events.append((zone, event, counts_json))
            zone_results.append(
                {
                    "zone_id": zone.zone_id,
                    "rule_hits": {rule.value: idx for rule, idx in hits.items()},
                    "counts": counts_json,
                    "active_rule": zone.active_rule.value,
                    "state": zone.machine.state,
                    "debounced_count": zone.machine.debounced_count,
                    "enabled": zone.enabled,
                    "needs_review": zone.needs_review,
                }
            )
        t_rules = now_ms()

        timings = Timings(
            queue_wait_ms=frame.t_dequeued_ms - frame.t_server_recv_ms,
            decode_ms=t_decoded - t_start,
            infer_ms=t_inferred - t_decoded,
            rules_ms=t_rules - t_inferred,
        )
        result = FrameResult(
            camera_id=frame.camera_id,
            seq=frame.seq,
            t_server_recv_ms=frame.t_server_recv_ms,
            t_processed_ms=t_rules,
            w=w,
            h=h,
            t_phone_ms=frame.t_phone_ms,
            detections=detections,
            zones=zone_results,
            timings=timings,
        )
        if self.on_event is not None:
            for zone, event, counts_json in pending_events:
                self.on_event(frame, result, zone, event, counts_json)
        self.publish(result, frame.jpeg)
        t_published = now_ms()
        timings.publish_ms = t_published - t_rules
        timings.total_ms = t_published - frame.t_server_recv_ms
        if self.recorder is not None:
            try:
                self.recorder.record(frame, result)
            except OSError:
                log.exception("recording failed camera=%s", frame.camera_id)
        frame.image = None  # release pixels as soon as possible (F-PRIV-1)
        return result
