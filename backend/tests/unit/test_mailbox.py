"""M3: latest-frame mailbox, FIFO baseline, round-robin scheduler and the frame-rate guard (E.3)."""

from __future__ import annotations

import threading
import time

import pytest

from publicsafe.core.mailbox import FifoSlot, FrameRateGuard, LatestFrameSlot, Scheduler
from publicsafe.core.models import Frame


def frame(camera: str, seq: int) -> Frame:
    return Frame(camera, seq, 0.0, 0.0, 4, 4, jpeg=f"{camera}:{seq}".encode())


def test_latest_slot_replaces_unread() -> None:
    slot = LatestFrameSlot()
    assert slot.take() is None
    assert slot.put(frame("a", 1)) is False
    assert slot.put(frame("a", 2)) is True
    assert slot.dropped_replaced == 1 and slot.has_unread
    taken = slot.take()
    assert taken is not None and taken.seq == 2
    assert slot.take() is None and not slot.has_unread


def test_fifo_slot_keeps_order() -> None:
    q = FifoSlot()
    for i in range(3):
        q.put(frame("a", i))
    assert [q.take().seq for _ in range(3)] == [0, 1, 2]  # type: ignore[union-attr]
    assert q.take() is None and len(q) == 0


def test_scheduler_round_robin_and_isolation() -> None:
    s = Scheduler("latest")
    for cam in ("a", "b", "c"):
        for seq in (1, 2):
            s.put(frame(cam, seq))
    assert s.backlog() == 3
    taken = [s.next(timeout=0) for _ in range(3)]
    assert [f.camera_id for f in taken] == ["a", "b", "c"]  # type: ignore[union-attr]
    assert all(f.seq == 2 for f in taken)  # type: ignore[union-attr]
    assert all(f.jpeg == f"{f.camera_id}:2".encode() for f in taken)  # type: ignore[union-attr]
    assert s.next(timeout=0) is None
    assert {c: s.dropped_replaced(c) for c in "abc"} == {"a": 1, "b": 1, "c": 1}
    s.put(frame("b", 3))
    assert s.next(timeout=0.1).camera_id == "b"  # type: ignore[union-attr]
    s.remove("b")
    assert s.cameras() == ["a", "c"]


def test_scheduler_next_blocks_until_put() -> None:
    s = Scheduler()
    result: list[Frame | None] = []
    t = threading.Thread(target=lambda: result.append(s.next(timeout=2)))
    t.start()
    time.sleep(0.1)
    s.put(frame("a", 1))
    t.join(2)
    assert result and result[0] is not None and result[0].seq == 1
    assert s.next(timeout=0.05) is None


def test_three_producers_30fps_consumer_20fps_fair_share() -> None:
    """Each camera gets at least 6 fps and replaced frames are counted (Section L)."""
    s = Scheduler("latest")
    stop = threading.Event()
    seen: dict[str, int] = {"a": 0, "b": 0, "c": 0}
    mixups = 0

    def producer(cam: str) -> None:
        seq = 0
        while not stop.is_set():
            seq += 1
            s.put(frame(cam, seq))
            time.sleep(1 / 30)

    def consumer() -> None:
        nonlocal mixups
        while not stop.is_set():
            f = s.next(timeout=0.1)
            if f is None:
                continue
            if f.jpeg.split(b":")[0].decode() != f.camera_id:
                mixups += 1
            seen[f.camera_id] += 1
            time.sleep(1 / 20)  # processing cost -> 20 fps max

    threads = [threading.Thread(target=producer, args=(c,)) for c in "abc"]
    threads.append(threading.Thread(target=consumer))
    for t in threads:
        t.start()
    time.sleep(2.0)
    stop.set()
    for t in threads:
        t.join(2)
    assert mixups == 0
    assert all(seen[c] >= 12 for c in "abc"), seen  # >= 6 fps over 2 s
    assert all(s.dropped_replaced(c) > 0 for c in "abc")
    assert max(seen.values()) / min(seen.values()) <= 1.3


def test_fifo_strategy_grows_unbounded() -> None:
    s = Scheduler("fifo")
    for i in range(200):
        s.put(frame("a", i))
        s.put(frame("b", i))
    assert s.backlog() == 400  # nothing was replaced; a slow consumer only sees old frames
    first = s.next(timeout=0)
    assert first is not None and first.seq == 0
    s.remove("a")
    assert s.backlog() == 200 - 0 - 0 and all(f.camera_id == "b" for f in list(s._fifo._queue))


def test_invalid_strategy() -> None:
    with pytest.raises(ValueError):
        Scheduler("random")  # type: ignore[arg-type]


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_rate_guard_allows_up_to_2x_target() -> None:
    clock = Clock()
    g = FrameRateGuard(10, clock=clock)
    results = []
    for i in range(25):
        clock.t = 100.0 + i * 0.03  # ~33 fps
        results.append(g.observe())
    allowed = [a for a, _ in results]
    assert allowed[:20] == [True] * 20 and allowed[20:] == [False] * 5
    assert not any(abusive for _, abusive in results)  # under 10 s


def test_rate_guard_abuse_after_sustained_3x() -> None:
    clock = Clock()
    g = FrameRateGuard(10, abuse_seconds=10, clock=clock)
    abusive = False
    for i in range(480):  # 40 fps for 12 s
        clock.t = 100.0 + i * 0.025
        _, abusive = g.observe()
        if abusive:
            break
    # the rate first exceeds 3x after 31 frames (0.775 s); abuse is declared 10 s later
    assert abusive and 10.5 <= clock.t - 100.0 <= 11.0
    clock.t += 5.0  # pause -> rate drops -> no longer abusive
    assert g.observe() == (True, False)
    g.set_target(1)
    assert g.target_fps == 1
