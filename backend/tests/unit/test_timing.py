"""M2: clock-offset estimator and percentile helpers (H.5, F.7)."""

from __future__ import annotations

import pytest

from publicsafe.core.timing import ClockOffsetEstimator, RingBuffer, percentile, percentiles


def test_offset_first_sample_then_ema() -> None:
    est = ClockOffsetEstimator(alpha=0.2)
    assert est.offset_ms is None and est.uncertainty_ms is None
    # phone clock 40 ms behind the server, 20 ms RTT: server_recv = phone + 40 + 10
    assert est.update(t_phone_ms=1000, last_rtt_ms=20, t_server_recv_ms=1050) == pytest.approx(40)
    assert est.rtt_ms == 20 and est.uncertainty_ms == 10 and est.samples == 1
    est.update(2000, 20, 2060)  # sample 50 -> 40 + 0.2 * 10 = 42
    assert est.offset_ms == pytest.approx(42)
    est.update(3000, 0, 3042)  # sample 42 -> unchanged
    assert est.offset_ms == pytest.approx(42)


def test_offset_alpha_validation() -> None:
    with pytest.raises(ValueError):
        ClockOffsetEstimator(alpha=0)
    with pytest.raises(ValueError):
        ClockOffsetEstimator(alpha=1.5)


def test_percentile_nearest_rank() -> None:
    values = list(range(1, 101))
    assert percentile(values, 50) == 50
    assert percentile(values, 90) == 90
    assert percentile(values, 99) == 99
    assert percentile(values, 100) == 100
    assert percentile(values, 0) == 1
    assert percentile([7.5], 95) == 7.5
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_percentiles_and_ring_buffer() -> None:
    assert percentiles([]) == {"p50": None, "p90": None, "p95": None, "p99": None}
    assert percentiles([3, 1, 2], (50,)) == {"p50": 2.0}
    buf = RingBuffer(maxlen=3)
    for v in (10, 20, 30, 40):
        buf.add(v)
    assert len(buf) == 3 and buf.values() == [20.0, 30.0, 40.0]
    assert buf.percentiles((50, 99)) == {"p50": 30.0, "p99": 40.0}
    buf.clear()
    assert len(buf) == 0 and buf.percentiles()["p50"] is None
