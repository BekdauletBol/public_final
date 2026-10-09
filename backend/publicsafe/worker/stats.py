"""Per-camera counters, fps windows and span percentiles (H.5)."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from publicsafe.core.models import FrameResult
from publicsafe.core.timing import RingBuffer, percentiles

FPS_WINDOW_S = 5.0
KEEP_S = 60.0


class CameraStats:
    """Thread-safe: the event loop records receptions, the worker records results."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.created = clock()
        self.frames_in = 0
        self.bytes_in = 0
        self.processed = 0
        self.dropped_replaced = 0
        self.dropped_ratelimit = 0
        self.decode_errors = 0
        self.out_of_order = 0
        self.worker_errors = 0
        self.last_w: int | None = None
        self.last_h: int | None = None
        self.processed_seq: int | None = None
        self._recv_times: deque[float] = deque()
        self._proc_times: deque[float] = deque()
        self.total = RingBuffer(2000)
        self.infer = RingBuffer(2000)
        self.decode = RingBuffer(2000)
        self.queue_wait = RingBuffer(2000)
        self.e2e = RingBuffer(2000)
        self._sample_base: dict[str, int] = {}
        self._sample_t = clock()

    # --- recording -----------------------------------------------------------

    def on_received(
        self, nbytes: int, *, dropped_ratelimit: bool = False, replaced: bool = False
    ) -> None:
        now = self.clock()
        with self.lock:
            self.frames_in += 1
            self.bytes_in += nbytes
            if dropped_ratelimit:
                self.dropped_ratelimit += 1
            if replaced:
                self.dropped_replaced += 1
            self._recv_times.append(now)
            self._prune(self._recv_times, now)

    def on_processed(self, result: FrameResult, t_phone_ms: float, offset_ms: float | None) -> None:
        now = self.clock()
        t = result.timings
        with self.lock:
            self.processed += 1
            self.processed_seq = result.seq
            self.last_w, self.last_h = result.w, result.h
            self._proc_times.append(now)
            self._prune(self._proc_times, now)
            self.total.add(t.total_ms)
            self.infer.add(t.infer_ms)
            self.decode.add(t.decode_ms)
            self.queue_wait.add(t.queue_wait_ms)
            if offset_ms is not None:
                # H.5: total + (recv - (t_phone + offset))
                self.e2e.add(t.total_ms + (result.t_server_recv_ms - (t_phone_ms + offset_ms)))

    def on_decode_error(self) -> None:
        with self.lock:
            self.decode_errors += 1

    def on_worker_error(self) -> None:
        with self.lock:
            self.worker_errors += 1

    def on_out_of_order(self) -> None:
        with self.lock:
            self.out_of_order += 1

    @staticmethod
    def _prune(times: deque[float], now: float) -> None:
        while times and now - times[0] > KEEP_S:
            times.popleft()

    # --- reading -------------------------------------------------------------

    def _fps(self, times: deque[float], now: float) -> float:
        window = min(FPS_WINDOW_S, max(now - self.created, 1e-3))
        count = sum(1 for t in times if now - t <= FPS_WINDOW_S)
        return count / window

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        with self.lock:
            dropped = self.dropped_replaced + self.dropped_ratelimit
            return {
                "frames_in": self.frames_in,
                "bytes_in": self.bytes_in,
                "processed": self.processed,
                "processed_seq": self.processed_seq,
                "dropped_replaced": self.dropped_replaced,
                "dropped_ratelimit": self.dropped_ratelimit,
                "decode_errors": self.decode_errors,
                "worker_errors": self.worker_errors,
                "out_of_order": self.out_of_order,
                "fps_in": round(self._fps(self._recv_times, now), 2),
                "fps_processed": round(self._fps(self._proc_times, now), 2),
                "drop_ratio": round(dropped / self.frames_in, 4) if self.frames_in else 0.0,
                "last_w": self.last_w,
                "last_h": self.last_h,
                "spans": {
                    "total": self.total.percentiles(),
                    "infer": self.infer.percentiles(),
                    "decode": self.decode.percentiles(),
                    "queue_wait": self.queue_wait.percentiles(),
                    "e2e_estimate": self.e2e.percentiles(),
                },
            }

    def sample(self) -> dict[str, Any]:
        """Counters since the previous sample (for ``MetricSample`` rows every 5 s)."""
        now = self.clock()
        with self.lock:
            window = max(now - self._sample_t, 1e-3)
            base = self._sample_base
            current = {
                "frames_in": self.frames_in,
                "processed": self.processed,
                "dropped_replaced": self.dropped_replaced,
                "dropped_ratelimit": self.dropped_ratelimit,
                "decode_errors": self.decode_errors,
                "out_of_order": self.out_of_order,
            }
            delta = {k: v - base.get(k, 0) for k, v in current.items()}
            self._sample_base = current
            self._sample_t = now
            total = percentiles(self.total.values(), (50, 95, 99))
            infer = percentiles(self.infer.values(), (50, 95))
            qw = percentiles(self.queue_wait.values(), (50,))
            e2e = percentiles(self.e2e.values(), (50,))
            return {
                "window_s": round(window, 3),
                "fps_in": round(delta["frames_in"] / window, 3),
                "fps_processed": round(delta["processed"] / window, 3),
                "dropped_replaced": delta["dropped_replaced"],
                "dropped_ratelimit": delta["dropped_ratelimit"],
                "decode_errors": delta["decode_errors"],
                "out_of_order": delta["out_of_order"],
                "total_p50": total["p50"],
                "total_p95": total["p95"],
                "total_p99": total["p99"],
                "infer_p50": infer["p50"],
                "infer_p95": infer["p95"],
                "queue_wait_p50": qw["p50"],
                "e2e_estimate_p50": e2e["p50"],
                "last_w": self.last_w,
                "last_h": self.last_h,
            }
