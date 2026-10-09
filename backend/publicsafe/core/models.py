"""Plain data records shared by the ingest, worker and evaluation code (H.1, F.8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(eq=False)
class Frame:
    """One received JPEG frame and its timing. ``image`` is filled by the decoder."""

    camera_id: str
    seq: int
    t_phone_ms: float
    t_server_recv_ms: float
    w: int
    h: int
    jpeg: bytes
    t_dequeued_ms: float | None = None
    image: np.ndarray | None = None


@dataclass(frozen=True)
class Detection:
    """A person box in pixels (``x1, y1, x2, y2``) with confidence."""

    bbox_xyxy: tuple[float, float, float, float]
    conf: float
    cls: int = 0
    track_id: int | None = None

    @property
    def anchors(self) -> dict[str, tuple[float, float]]:
        """Bottom-left, bottom-centre and bottom-right foot points (H.3)."""
        x1, _y1, x2, y2 = self.bbox_xyxy
        return {"bl": (x1, y2), "bc": ((x1 + x2) / 2.0, y2), "br": (x2, y2)}

    def to_json(self) -> dict[str, Any]:
        return {
            "bbox": [round(v, 1) for v in self.bbox_xyxy],
            "conf": round(self.conf, 3),
            "anchors": {k: [round(v[0], 1), round(v[1], 1)] for k, v in self.anchors.items()},
            "track_id": self.track_id,
        }


@dataclass
class Timings:
    """Per-frame spans in milliseconds (H.5)."""

    queue_wait_ms: float = 0.0
    decode_ms: float = 0.0
    infer_ms: float = 0.0
    rules_ms: float = 0.0
    publish_ms: float = 0.0
    total_ms: float = 0.0

    def to_json(self) -> dict[str, float]:
        return {
            "queue_wait_ms": round(self.queue_wait_ms, 2),
            "decode_ms": round(self.decode_ms, 2),
            "infer_ms": round(self.infer_ms, 2),
            "rules_ms": round(self.rules_ms, 2),
            "total_ms": round(self.total_ms, 2),
        }


@dataclass
class FrameResult:
    """What the dashboard receives as ``frame_result`` (F.8); zones arrive in M4."""

    camera_id: str
    seq: int
    t_server_recv_ms: float
    t_processed_ms: float
    w: int
    h: int
    t_phone_ms: float = 0.0
    detections: list[Detection] = field(default_factory=list)
    zones: list[dict[str, Any]] = field(default_factory=list)
    timings: Timings = field(default_factory=Timings)

    def to_json(self) -> dict[str, Any]:
        return {
            "type": "frame_result",
            "camera_id": self.camera_id,
            "seq": self.seq,
            "t_server_recv_ms": round(self.t_server_recv_ms, 1),
            "t_processed_ms": round(self.t_processed_ms, 1),
            "w": self.w,
            "h": self.h,
            "detections": [d.to_json() for d in self.detections],
            "zones": self.zones,
            "timings": self.timings.to_json(),
        }
