"""In-process sliding-window rate limiter keyed by client IP (F-SEC-2, ADR 0002).

Deterministic: the clock is injectable, so tests never sleep. One limiter per
route; each limiter can enforce several ``(limit, window_seconds)`` rules at
once, e.g. redeem = 10/min and 30/10 min.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from publicsafe.api.errors import ApiError

Rule = tuple[int, float]  # (max_requests, window_seconds)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after_s: float  # 0 when allowed


class SlidingWindowLimiter:
    """Allow at most ``limit`` hits per ``window`` seconds for every rule, per key."""

    def __init__(self, rules: list[Rule], clock: Callable[[], float] = time.monotonic) -> None:
        if not rules:
            raise ValueError("at least one rule is required")
        for limit, window in rules:
            if limit < 1 or window <= 0:
                raise ValueError(f"invalid rule {(limit, window)}")
        self.rules = sorted(rules, key=lambda r: r[1])
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._max_window = max(w for _, w in rules)

    def _prune(self, hits: deque[float], now: float) -> None:
        while hits and now - hits[0] >= self._max_window:
            hits.popleft()

    def check(self, key: str) -> Decision:
        """Decide without recording a hit."""
        now = self.clock()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return Decision(True, 0.0)
            self._prune(hits, now)
            return self._decide(hits, now)

    def hit(self, key: str) -> Decision:
        """Record one request and return whether it is allowed.

        A rejected request is not recorded, so a client that keeps retrying is
        not punished beyond the window.
        """
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            self._prune(hits, now)
            decision = self._decide(hits, now)
            if decision.allowed:
                hits.append(now)
            elif not hits:
                del self._hits[key]
            return decision

    def _decide(self, hits: deque[float], now: float) -> Decision:
        retry_after = 0.0
        for limit, window in self.rules:
            in_window = [t for t in hits if now - t < window]
            if len(in_window) >= limit:
                oldest = in_window[-limit]
                retry_after = max(retry_after, window - (now - oldest))
        return Decision(retry_after <= 0, max(retry_after, 0.0))

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def active_keys(self) -> int:
        with self._lock:
            return len(self._hits)


def client_ip(request: Request) -> str:
    """The direct peer address; proxies are not trusted (local deployment)."""
    return request.client.host if request.client else "unknown"


def enforce(limiter: SlidingWindowLimiter, request: Request) -> None:
    """Record a hit for the request's IP or raise 429 with ``Retry-After``."""
    decision = limiter.hit(client_ip(request))
    if not decision.allowed:
        retry = max(1, math.ceil(decision.retry_after_s))
        raise ApiError(
            429,
            "rate_limited",
            f"Too many requests; retry in {retry} s",
            {"retry_after_s": retry},
            headers={"Retry-After": str(retry)},
        )
