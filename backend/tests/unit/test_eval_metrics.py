"""M5: matching and metric functions of ``publicsafe.eval.metrics`` on hand-built cases (I.4)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from publicsafe.eval import metrics as m


def test_iou_matrix_values() -> None:
    a = np.array([[0, 0, 10, 10]])
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]])
    assert m.iou_matrix(a, b).tolist() == [[1.0, pytest.approx(50 / 150), 0.0]]
    assert m.iou_matrix(np.zeros((0, 4)), b).shape == (0, 3)


def test_hungarian_prefers_the_global_best_assignment() -> None:
    # det 0 overlaps both GTs; greedy-by-det would steal GT 0 from det 1.
    dets = np.array([[0, 0, 10, 10], [1, 0, 11, 10]])
    gts = np.array([[1, 0, 11, 10], [0, 0, 10, 10]])
    match = m.match_frame(dets, gts)
    assert match.pairs == [(0, 1), (1, 0)]
    assert match.unmatched_dets == [] and match.unmatched_gts == []


def test_detection_on_ignore_region_is_dropped_not_fp() -> None:
    dets = np.array([[0, 0, 10, 10], [50, 50, 60, 60], [100, 100, 110, 110]])
    gts = np.array([[0, 0, 10, 10]])
    ignores = np.array([[50, 50, 60, 61]])
    match = m.match_frame(dets, gts, ignores)
    assert match.pairs == [(0, 0)]
    assert match.ignored_dets == [1]
    assert match.unmatched_dets == [2]


def test_below_threshold_is_unmatched() -> None:
    match = m.match_frame(np.array([[0, 0, 10, 10]]), np.array([[6, 0, 16, 10]]))
    assert match.pairs == [] and match.unmatched_dets == [0] and match.unmatched_gts == [0]


def frame(  # type: ignore[no-untyped-def]
    pairs, unmatched_dets=(), unmatched_gts=(), hits=(), in_zone=(), ignored=()
) -> m.ZoneFrame:
    match = m.FrameMatch(list(pairs), list(unmatched_dets), list(ignored), list(unmatched_gts))
    return m.ZoneFrame(1, match, {"r": list(hits), "s": []}, list(in_zone))


def test_person_counts_every_case() -> None:
    # matched in/in (TP), matched out/in (FP), matched in/out (FN), matched out/out (TN),
    # unmatched det positive (FP), unmatched det negative (TN), missed GT in zone (FN, missed),
    # missed GT outside (nothing), ignored det positive (nothing).
    zf = frame(
        pairs=[(0, 0), (1, 1), (2, 2), (3, 3)],
        unmatched_dets=[4, 5],
        unmatched_gts=[4, 5],
        hits=[0, 1, 4, 6],
        in_zone=[1, 0, 1, 0, 1, 0],
        ignored=[6],
    )
    assert m.person_counts([zf], "r").tolist() == [[1, 2, 2, 2, 1]]
    assert zf.counted_hits("r") == {0, 1, 4}


def test_rates_and_nan_on_empty() -> None:
    assert m.rates(8, 2, 2, 8) == {"precision": 0.8, "recall": 0.8, "f1": 0.8, "fpr": 0.2}
    assert math.isnan(m.rates(0, 0, 0, 0)["precision"])


def test_bootstrap_is_deterministic_and_brackets_the_estimate() -> None:
    rng = np.random.default_rng(0)
    tp, fp, fn = rng.integers(0, 5, 200), rng.integers(0, 2, 200), rng.integers(0, 2, 200)
    counts = np.column_stack([tp, fp, fn, np.zeros(200), np.zeros(200)]).astype(np.int64)
    a = m.bootstrap_ci(counts, 1000, 1234)
    b = m.bootstrap_ci(counts, 1000, 1234)
    assert a == b
    precision = tp.sum() / (tp.sum() + fp.sum())
    assert a["precision"][0] < precision < a["precision"][1]


def test_mcnemar_exact() -> None:
    correct = np.array([[True, False]] * 7 + [[False, True]] * 1 + [[True, True]] * 50)
    out = m.mcnemar_matrix(correct, ["a", "b"])
    assert (out["a"]["b"]["b"], out["a"]["b"]["c"]) == (7, 1)
    assert out["a"]["b"]["p"] == pytest.approx(0.0703125)  # 2 * P(X <= 1), X ~ Bin(8, 0.5)
    assert out["b"]["a"]["p"] == out["a"]["b"]["p"]


def test_event_matching_greedy_nearest_and_duplicates() -> None:
    em = m.match_events(pred=[10, 13, 50, 200], truth=[12, 60], tolerance=10)
    assert em.pairs == [(13, 12), (50, 60)]
    assert em.duplicates == 1  # frame 10 is within tolerance of 12, which 13 already took
    s = em.summary()
    assert s["event_precision"] == 0.5 and s["event_recall"] == 1.0
    assert s["timing_err_median"] == 5.5 and s["duplicate_rate"] == 0.25


def test_entered_frames_uses_the_live_state_machine() -> None:
    counts = [0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 1]
    assert m.entered_frames(counts, list(range(1, 14)), 30, 3, 5) == [4, 13]
    assert m.entered_frames(counts, list(range(1, 14)), 30, 1, 1) == [2, 11]


def test_cohens_kappa() -> None:
    assert m.cohens_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert m.cohens_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == 0.0
    # 20 items: 15 agree; p_a = 0.5, p_b = 0.45 -> chance 0.5 -> kappa 0.5
    a = [1] * 10 + [0] * 10
    b = [1] * 7 + [0] * 3 + [1] * 2 + [0] * 8
    assert m.cohens_kappa(a, b) == pytest.approx((0.75 - 0.5) / 0.5)
