"""Detector protocol and the scripted FakeDetector (H.2). UltralyticsDetector arrives in M4."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np

from publicsafe.core.models import Detection


class Detector(Protocol):
    """Anything that turns a BGR image into person detections."""

    name: str

    def detect(self, image: np.ndarray) -> list[Detection]: ...


class FakeDetector:
    """Returns scripted detections frame by frame (cycling); optional sleep per call.

    The script is a list of frames, each a list of ``{"bbox": [x1, y1, x2, y2],
    "conf": 0.9}`` dicts in pixels (``TEST_MODE`` endpoint ``POST /api/test/detector``).
    """

    name = "fake"

    def __init__(
        self, script: Sequence[Sequence[dict[str, Any]]] | None = None, sleep_ms: float = 0.0
    ) -> None:
        self._lock = threading.Lock()
        self._script: list[list[Detection]] = []
        self._index = 0
        self.sleep_ms = sleep_ms
        self.calls = 0
        self.set_script(script or [])

    def set_script(self, script: Sequence[Sequence[dict[str, Any]]]) -> None:
        frames: list[list[Detection]] = []
        for frame in script:
            dets = []
            for item in frame:
                x1, y1, x2, y2 = (float(v) for v in item["bbox"])
                dets.append(Detection((x1, y1, x2, y2), float(item.get("conf", 0.9))))
            frames.append(dets)
        with self._lock:
            self._script = frames
            self._index = 0

    def detect(self, image: np.ndarray) -> list[Detection]:
        if self.sleep_ms > 0:
            time.sleep(self.sleep_ms / 1000.0)
        with self._lock:
            self.calls += 1
            if not self._script:
                return []
            dets = self._script[self._index % len(self._script)]
            self._index += 1
            return list(dets)


class NullDetector:
    """No detection at all (M3 pass-through)."""

    name = "none"

    def detect(self, image: np.ndarray) -> list[Detection]:
        return []
