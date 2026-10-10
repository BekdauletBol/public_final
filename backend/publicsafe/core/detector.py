"""Detector protocol, the scripted FakeDetector and the YOLOv8n adapter (H.2)."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from publicsafe.core.models import Detection

log = logging.getLogger("publicsafe.detector")


class Detector(Protocol):
    """Anything that turns a BGR image into person detections."""

    name: str

    def detect(self, image: np.ndarray, conf: float | None = None) -> list[Detection]: ...


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

    def detect(self, image: np.ndarray, conf: float | None = None) -> list[Detection]:
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

    def detect(self, image: np.ndarray, conf: float | None = None) -> list[Detection]:
        return []


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pick_device(override: str | None = None) -> str:
    """``mps`` -> ``cuda`` -> ``cpu`` unless ``INFERENCE_DEVICE`` says otherwise (F-CV-1)."""
    if override:
        return override
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class UltralyticsDetector:
    """YOLOv8n person detector behind the :class:`Detector` protocol.

    Imports ultralytics lazily so that ``core`` stays importable without it
    (and so the AGPL dependency is confined to this adapter, see ADR 0009).
    """

    name = "yolo"

    def __init__(
        self,
        model_path: str | Path = "yolov8n.pt",
        device: str | None = None,
        conf: float = 0.30,
        imgsz: int = 640,
        half: bool = False,
    ) -> None:
        from ultralytics.models import YOLO
        from ultralytics.utils.downloads import attempt_download_asset

        path = Path(model_path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            attempt_download_asset(str(path))
        self.model_path = path
        self.model_sha256 = sha256_of(path)
        self.device = pick_device(device)
        self.conf = conf
        self.imgsz = imgsz
        self.half = half and self.device != "cpu"
        self._model = YOLO(str(path))
        self._lock = threading.Lock()
        self.warmup_ms: float | None = None

    def warm_up(self) -> float:
        """One dummy frame so the first real frame is not slow; returns milliseconds."""
        dummy = np.zeros((360, 640, 3), np.uint8)
        t0 = time.perf_counter()
        self.detect(dummy)
        self.warmup_ms = (time.perf_counter() - t0) * 1000.0
        log.info("detector warm-up on %s took %.0f ms", self.device, self.warmup_ms)
        return self.warmup_ms

    def detect(self, image: np.ndarray, conf: float | None = None) -> list[Detection]:
        with self._lock:
            results = self._model.predict(
                image,
                conf=self.conf if conf is None else conf,
                classes=[0],
                device=self.device,
                imgsz=self.imgsz,
                half=self.half,
                verbose=False,
            )
        out: list[Detection] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue
            xyxy = _to_numpy(boxes.xyxy)
            confs = _to_numpy(boxes.conf)
            for (x1, y1, x2, y2), c in zip(xyxy, confs, strict=True):
                out.append(Detection((float(x1), float(y1), float(x2), float(y2)), float(c)))
        return out


def _to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "cpu"):
        return np.asarray(value.cpu().numpy())
    return np.asarray(value)
