"""Per-camera latest-frame mailbox, the FIFO baseline and the round-robin scheduler (E.3, H.6).

Thread-safe: the event loop produces frames, the single worker thread consumes.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Literal

from publicsafe.core.models import Frame

Strategy = Literal["latest", "fifo"]


class LatestFrameSlot:
    """Holds at most one unread frame; a newer frame replaces an unread one."""

    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._lock = threading.Lock()
        self.dropped_replaced = 0
        self.put_count = 0

    def put(self, frame: Frame) -> bool:
        """Store ``frame``; returns True when an unread frame was replaced."""
        with self._lock:
            replaced = self._frame is not None
            if replaced:
                self.dropped_replaced += 1
            self._frame = frame
            self.put_count += 1
            return replaced

    def take(self) -> Frame | None:
        with self._lock:
            frame, self._frame = self._frame, None
            return frame

    @property
    def has_unread(self) -> bool:
        return self._frame is not None

    def clear(self) -> None:
        with self._lock:
            self._frame = None


class FifoSlot:
    """Unbounded FIFO shared by all cameras: the paper's baseline, experiments only."""

    def __init__(self, maxlen: int | None = None) -> None:
        self._queue: deque[Frame] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def put(self, frame: Frame) -> None:
        with self._lock:
            self._queue.append(frame)

    def take(self) -> Frame | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None

    def __len__(self) -> int:
        return len(self._queue)

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()


class Scheduler:
    """Hands the worker the next frame.

    ``latest``: one :class:`LatestFrameSlot` per camera, visited round-robin so
    K active cameras each get one turn per round. ``fifo``: one shared queue
    in arrival order (unbounded; measured by experiment E5).
    """

    def __init__(self, strategy: Strategy = "latest") -> None:
        if strategy not in ("latest", "fifo"):
            raise ValueError(f"unknown strategy {strategy!r}")
        self.strategy: Strategy = strategy
        self._slots: dict[str, LatestFrameSlot] = {}
        self._order: list[str] = []
        self._cursor = 0
        self._fifo = FifoSlot()
        self._cv = threading.Condition()
        self.puts = 0

    # --- producer side (event loop) -----------------------------------------

    def put(self, frame: Frame) -> bool:
        """Enqueue a frame; returns True when it replaced an unread frame (latest only)."""
        with self._cv:
            self.puts += 1
            if self.strategy == "fifo":
                self._fifo.put(frame)
                replaced = False
            else:
                slot = self._slots.get(frame.camera_id)
                if slot is None:
                    slot = LatestFrameSlot()
                    self._slots[frame.camera_id] = slot
                    self._order.append(frame.camera_id)
                replaced = slot.put(frame)
            self._cv.notify()
            return replaced

    def remove(self, camera_id: str) -> None:
        """Forget a camera (revoked or deleted); its unread frame is discarded."""
        with self._cv:
            if camera_id in self._slots:
                del self._slots[camera_id]
                self._order.remove(camera_id)
                self._cursor = 0
            if self.strategy == "fifo":
                remaining = [f for f in list(self._fifo._queue) if f.camera_id != camera_id]
                self._fifo.clear()
                for f in remaining:
                    self._fifo.put(f)

    # --- consumer side (worker thread) --------------------------------------

    def next(self, timeout: float | None = None) -> Frame | None:
        """Block until a frame is available (or ``timeout`` elapses) and return it."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while True:
                frame = self._take_locked()
                if frame is not None:
                    return frame
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._cv.wait(remaining)

    def _take_locked(self) -> Frame | None:
        if self.strategy == "fifo":
            return self._fifo.take()
        n = len(self._order)
        for step in range(n):
            index = (self._cursor + step) % n
            slot = self._slots[self._order[index]]
            frame = slot.take()
            if frame is not None:
                self._cursor = (index + 1) % n
                return frame
        return None

    # --- introspection -----------------------------------------------------

    def dropped_replaced(self, camera_id: str) -> int:
        slot = self._slots.get(camera_id)
        return slot.dropped_replaced if slot else 0

    def backlog(self) -> int:
        """Unread frames: at most one per camera under ``latest``, unbounded under ``fifo``."""
        if self.strategy == "fifo":
            return len(self._fifo)
        return sum(1 for s in self._slots.values() if s.has_unread)

    def cameras(self) -> list[str]:
        return list(self._order)


class FrameRateGuard:
    """Per-camera limits on frames *received* (E.3).

    ``allow(t)`` is False for frames above ``2 x target`` in the last second
    (dropped, counted as ``dropped_ratelimit``). ``abusive(t)`` becomes True
    once the rate stays above ``3 x target`` for ``abuse_seconds`` (close 4008).
    Both are evaluated on every received frame, including dropped ones.
    """

    def __init__(
        self,
        target_fps: float,
        *,
        abuse_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.target_fps = float(target_fps)
        self.abuse_seconds = abuse_seconds
        self.clock = clock
        self._recent: deque[float] = deque()
        self._abuse_since: float | None = None

    def set_target(self, target_fps: float) -> None:
        self.target_fps = float(target_fps)

    def observe(self) -> tuple[bool, bool]:
        """Record one received frame: ``(allowed, abusive)``."""
        now = self.clock()
        self._recent.append(now)
        while self._recent and now - self._recent[0] >= 1.0:
            self._recent.popleft()
        rate = len(self._recent)
        allowed = rate <= 2 * self.target_fps
        if rate > 3 * self.target_fps:
            if self._abuse_since is None:
                self._abuse_since = now
        else:
            self._abuse_since = None
        abusive = self._abuse_since is not None and now - self._abuse_since >= self.abuse_seconds
        return allowed, abusive

    @property
    def rate(self) -> int:
        return len(self._recent)
