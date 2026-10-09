"""M1: deterministic sliding-window limiter (F-SEC-2)."""

from __future__ import annotations

import threading

import pytest

from publicsafe.api.ratelimit import SlidingWindowLimiter


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


def test_allows_up_to_limit_then_blocks_with_retry_after(clock: Clock) -> None:
    lim = SlidingWindowLimiter([(5, 60.0)], clock)
    for _ in range(5):
        assert lim.hit("ip").allowed
    d = lim.hit("ip")
    assert not d.allowed
    assert d.retry_after_s == pytest.approx(60.0)
    clock.t += 30
    d = lim.hit("ip")
    assert not d.allowed and d.retry_after_s == pytest.approx(30.0)
    clock.t += 30.001
    assert lim.hit("ip").allowed  # oldest hit left the window


def test_window_slides_not_resets(clock: Clock) -> None:
    lim = SlidingWindowLimiter([(3, 10.0)], clock)
    for i in range(3):
        clock.t = 1000.0 + i  # hits at 1000, 1001, 1002
        assert lim.hit("k").allowed
    clock.t = 1009.5
    assert not lim.hit("k").allowed
    clock.t = 1010.0  # first hit expires exactly now
    assert lim.hit("k").allowed
    clock.t = 1010.5
    assert not lim.hit("k").allowed  # 1001, 1002, 1010 are in the window


def test_multiple_rules_enforced_together(clock: Clock) -> None:
    lim = SlidingWindowLimiter([(10, 60.0), (30, 600.0)], clock)
    allowed = 0
    for minute in range(5):
        clock.t = 1000.0 + minute * 61
        for _ in range(10):
            allowed += 1 if lim.hit("k").allowed else 0
        assert not lim.hit("k").allowed  # 11th in the minute
    assert allowed == 30  # the 10-minute rule capped the total
    clock.t = 1000.0 + 5 * 61
    d = lim.hit("k")
    assert not d.allowed and d.retry_after_s > 60


def test_keys_are_independent_and_rejections_not_recorded(clock: Clock) -> None:
    lim = SlidingWindowLimiter([(2, 60.0)], clock)
    assert lim.hit("a").allowed and lim.hit("a").allowed
    assert not lim.hit("a").allowed
    assert lim.hit("b").allowed
    clock.t += 60
    assert lim.hit("a").allowed  # the rejected attempt did not extend the block


def test_check_does_not_record(clock: Clock) -> None:
    lim = SlidingWindowLimiter([(1, 60.0)], clock)
    assert lim.check("k").allowed
    assert lim.check("k").allowed
    assert lim.hit("k").allowed
    assert not lim.check("k").allowed


def test_reset_and_memory(clock: Clock) -> None:
    lim = SlidingWindowLimiter([(1, 60.0)], clock)
    lim.hit("a")
    lim.hit("b")
    assert lim.active_keys() == 2
    lim.reset("a")
    assert lim.active_keys() == 1
    lim.reset()
    assert lim.active_keys() == 0


def test_invalid_rules() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter([])
    with pytest.raises(ValueError):
        SlidingWindowLimiter([(0, 60.0)])


def test_thread_safety(clock: Clock) -> None:
    lim = SlidingWindowLimiter([(100, 60.0)], clock)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(50):
            d = lim.hit("k")
            with lock:
                results.append(d.allowed)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 100
