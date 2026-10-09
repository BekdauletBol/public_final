"""Timing helpers: clock-offset estimation, ring buffers and percentiles (H.5, F.7)."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Iterable, Sequence


def now_ms() -> float:
    """Wall-clock milliseconds since the epoch (float, sub-millisecond resolution)."""
    return time.time() * 1000.0


class ClockOffsetEstimator:
    """Exponential moving average of ``t_server_recv - (t_phone + rtt/2)``.

    Updated on every ping that carries ``last_rtt_ms``. The estimate is only
    used for the ``e2e_estimate_ms`` metric, never for ordering or event times.
    """

    def __init__(self, alpha: float = 0.2) -> None:
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = alpha
        self.offset_ms: float | None = None
        self.rtt_ms: float | None = None
        self.samples = 0

    def update(self, t_phone_ms: float, last_rtt_ms: float, t_server_recv_ms: float) -> float:
        """Record one sample and return the new offset estimate (ms)."""
        sample = t_server_recv_ms - (t_phone_ms + last_rtt_ms / 2.0)
        if self.offset_ms is None:
            self.offset_ms = sample
        else:
            self.offset_ms = self.offset_ms + self.alpha * (sample - self.offset_ms)
        self.rtt_ms = float(last_rtt_ms)
        self.samples += 1
        return self.offset_ms

    @property
    def uncertainty_ms(self) -> float | None:
        """Half the last RTT: the offset cannot be known better than this."""
        return None if self.rtt_ms is None else self.rtt_ms / 2.0


def percentile(sorted_values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile of an ascending sequence (``p`` in 0..100)."""
    if not sorted_values:
        raise ValueError("no values")
    if not 0 <= p <= 100:
        raise ValueError("p must be within 0..100")
    rank = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return float(sorted_values[rank - 1])


def percentiles(
    values: Iterable[float], ps: Sequence[int] = (50, 90, 95, 99)
) -> dict[str, float | None]:
    """``{"p50": ..., "p90": ...}``; ``None`` for every key when there are no values."""
    ordered = sorted(values)
    if not ordered:
        return {f"p{p}": None for p in ps}
    return {f"p{p}": percentile(ordered, p) for p in ps}


class RingBuffer:
    """Fixed-size buffer of recent float samples (last 2,000 span totals per camera)."""

    def __init__(self, maxlen: int = 2000) -> None:
        self._values: deque[float] = deque(maxlen=maxlen)

    def add(self, value: float) -> None:
        self._values.append(float(value))

    def __len__(self) -> int:
        return len(self._values)

    def values(self) -> list[float]:
        return list(self._values)

    def percentiles(self, ps: Sequence[int] = (50, 90, 95, 99)) -> dict[str, float | None]:
        return percentiles(self._values, ps)

    def clear(self) -> None:
        self._values.clear()
