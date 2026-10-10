"""EVAL_RECORD=1: write each processed frame and its FrameResult for offline re-evaluation (H.7)."""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import IO, Any

from publicsafe.core.models import Frame, FrameResult

log = logging.getLogger("publicsafe.recorder")


class Recorder:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._handles: dict[Path, IO[str]] = {}
        self.frames_written = 0

    def _dir_for(self, camera_id: str) -> Path:
        day = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
        folder = self.root / camera_id / day
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def record(
        self, frame: Frame, result: FrameResult, extra: dict[str, Any] | None = None
    ) -> None:
        folder = self._dir_for(frame.camera_id)
        (folder / f"{frame.seq:08d}.jpg").write_bytes(frame.jpeg)  # bytes as received
        line = {**result.to_json(), "t_phone_ms": frame.t_phone_ms, **(extra or {})}
        with self._lock:
            handle = self._handles.get(folder)
            if handle is None:
                handle = (folder / "frames.jsonl").open("a", encoding="utf-8")
                self._handles[folder] = handle
            handle.write(json.dumps(line) + "\n")
            handle.flush()
            self.frames_written += 1

    def close(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                handle.close()
            self._handles.clear()
