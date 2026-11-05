"""Matching and metrics for the evaluation harness (I.4).

Pure functions over plain arrays, so every number in a report can be checked by
hand on a small synthetic sequence (``tests/cv/test_eval_metrics.py``).

Conventions, per (sequence, zone, rule):

- A *person instance* is a matched (detection, GT) pair, an unmatched detection
  or an unmatched GT person on an annotated frame.
- Matched pair: TP / FP / FN / TN from GT ``in_zone`` and the rule's decision.
- Unmatched detection: rule positive -> FP, negative -> TN.
- Unmatched GT with ``in_zone=1`` -> FN, also counted in ``fn_missed_detection``.
- Detections whose best IoU is with an ignore region are dropped everywhere,
  including the frame and event counts.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.stats import binomtest

from publicsafe.core.state_machine import ZoneStateMachine

IOU_THRESHOLD = 0.5
BOUNDARY_BIN_PX = 10
BOUNDARY_RANGE_PX = 100  # distances beyond +-100 px fall into the outermost bins
BOOTSTRAP_CHUNK = 250


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU of every box in ``a`` (N, 4) with every box in ``b`` (M, 4), ``x1, y1, x2, y2``."""
    a = np.asarray(a, dtype=float).reshape(-1, 4)
    b = np.asarray(b, dtype=float).reshape(-1, 4)
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(union > 0, inter / union, 0.0)


@dataclass(frozen=True)
class FrameMatch:
    pairs: list[tuple[int, int]]  # (detection index, counted GT index)
    unmatched_dets: list[int]
    ignored_dets: list[int]
    unmatched_gts: list[int]


def match_frame(
    dets: np.ndarray,
    gts: np.ndarray,
    ignores: np.ndarray | None = None,
    iou_threshold: float = IOU_THRESHOLD,
) -> FrameMatch:
    """Hungarian matching on IoU; unmatched detections on ignore regions are discarded."""
    dets = np.asarray(dets, dtype=float).reshape(-1, 4)
    gts = np.asarray(gts, dtype=float).reshape(-1, 4)
    pairs: list[tuple[int, int]] = []
    if len(dets) and len(gts):
        iou = iou_matrix(dets, gts)
        rows, cols = linear_sum_assignment(-iou)
        pairs = [
            (int(r), int(c)) for r, c in zip(rows, cols, strict=True) if iou[r, c] >= iou_threshold
        ]
    matched_d = {d for d, _ in pairs}
    matched_g = {g for _, g in pairs}
    rest = [d for d in range(len(dets)) if d not in matched_d]
    ignored: list[int] = []
    if rest and ignores is not None and len(ignores):
        best = iou_matrix(dets[rest], ignores).max(axis=1)
        ignored = [d for d, v in zip(rest, best, strict=True) if v >= iou_threshold]
    unmatched = [d for d in rest if d not in set(ignored)]
    return FrameMatch(
        pairs=sorted(pairs),
        unmatched_dets=unmatched,
        ignored_dets=ignored,
        unmatched_gts=[g for g in range(len(gts)) if g not in matched_g],
    )


@dataclass
class ZoneFrame:
    """One annotated frame of one zone: what the matcher and the rules said."""

    frame: int
    match: FrameMatch
    hits: Mapping[str, Sequence[int]]  # rule -> detection indices
    in_zone: Sequence[int]  # per counted GT person (0/1)
    gt_distance_px: Sequence[float] = ()  # signed, > 0 inside the polygon

    def counted_hits(self, rule: str) -> set[int]:
        return set(self.hits[rule]) - set(self.match.ignored_dets)


# --- person level ------------------------------------------------------------------


def person_counts(frames: Sequence[ZoneFrame], rule: str) -> np.ndarray:
    """Per frame ``[tp, fp, fn, tn, fn_missed_detection]`` (shape F x 5)."""
    out = np.zeros((len(frames), 5), dtype=np.int64)
    for i, zf in enumerate(frames):
        pos = zf.counted_hits(rule)
        tp = fp = fn = tn = missed = 0
        for d, g in zf.match.pairs:
            truth, pred = zf.in_zone[g] == 1, d in pos
            if truth and pred:
                tp += 1
            elif pred:
                fp += 1
            elif truth:
                fn += 1
            else:
                tn += 1
        for d in zf.match.unmatched_dets:
            if d in pos:
                fp += 1
            else:
                tn += 1
        for g in zf.match.unmatched_gts:
            if zf.in_zone[g] == 1:
                fn += 1
                missed += 1
        out[i] = (tp, fp, fn, tn, missed)
    return out


def _safe_div(num: float, den: float) -> float:
    return num / den if den else math.nan


def rates(tp: float, fp: float, fn: float, tn: float) -> dict[str, float]:
    return {
        "precision": _safe_div(tp, tp + fp),
        "recall": _safe_div(tp, tp + fn),
        "f1": _safe_div(2 * tp, 2 * tp + fp + fn),
        "fpr": _safe_div(fp, fp + tn),
    }


def bootstrap_ci(
    counts: np.ndarray, n: int, seed: int, level: float = 0.95
) -> dict[str, tuple[float, float]]:
    """Percentile CIs for precision, recall and F1 by resampling frames (rows of ``counts``).

    The same ``seed`` and number of frames give the same resamples, so CIs of
    different rules on one zone are paired.
    """
    nan = (math.nan, math.nan)
    frames = len(counts)
    if frames == 0 or n <= 0:
        return {"precision": nan, "recall": nan, "f1": nan}
    rng = np.random.default_rng(seed)
    c = counts[:, :3].astype(float)  # tp, fp, fn
    sums = []
    for start in range(0, n, BOOTSTRAP_CHUNK):
        k = min(BOOTSTRAP_CHUNK, n - start)
        weights = rng.multinomial(frames, np.full(frames, 1.0 / frames), size=k)
        sums.append(weights @ c)
    tp, fp, fn = np.concatenate(sums).T
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(tp + fp > 0, tp / (tp + fp), np.nan)
        r = np.where(tp + fn > 0, tp / (tp + fn), np.nan)
        f = np.where(2 * tp + fp + fn > 0, 2 * tp / (2 * tp + fp + fn), np.nan)
    lo, hi = 100 * (1 - level) / 2, 100 * (1 + level) / 2

    def ci(values: np.ndarray) -> tuple[float, float]:
        values = values[~np.isnan(values)]
        if len(values) == 0:
            return nan
        return float(np.percentile(values, lo)), float(np.percentile(values, hi))

    return {"precision": ci(p), "recall": ci(r), "f1": ci(f)}


def instance_correct(frames: Sequence[ZoneFrame], rules: Sequence[str]) -> np.ndarray:
    """Boolean matrix (instances x rules): was the rule right about this person instance?"""
    rows: list[list[bool]] = []
    for zf in frames:
        pos = {rule: zf.counted_hits(rule) for rule in rules}
        for d, g in zf.match.pairs:
            truth = zf.in_zone[g] == 1
            rows.append([(d in pos[r]) == truth for r in rules])
        for d in zf.match.unmatched_dets:
            rows.append([d not in pos[r] for r in rules])
        for g in zf.match.unmatched_gts:
            if zf.in_zone[g] == 1:
                rows.append([False] * len(rules))
    return np.array(rows, dtype=bool).reshape(-1, len(rules))


def mcnemar_matrix(correct: np.ndarray, rules: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Exact McNemar test for each pair of rules on paired per-instance outcomes.

    ``b`` = row rule right and column rule wrong, ``c`` = the reverse; the
    p-value is the two-sided binomial test of ``min(b, c)`` out of ``b + c``.
    """
    out: dict[str, dict[str, Any]] = {}
    for i, a in enumerate(rules):
        out[a] = {}
        for j, b in enumerate(rules):
            if i == j:
                continue
            nb = int(np.sum(correct[:, i] & ~correct[:, j]))
            nc = int(np.sum(~correct[:, i] & correct[:, j]))
            p = 1.0 if nb + nc == 0 else float(binomtest(min(nb, nc), nb + nc, 0.5).pvalue)
            out[a][b] = {"b": nb, "c": nc, "p": p}
    return out


def boundary_bins(frames: Sequence[ZoneFrame], rules: Sequence[str]) -> list[dict[str, Any]]:
    """Error rate per rule by signed distance of the GT bottom-centre to the zone edge.

    Bins are ``BOUNDARY_BIN_PX`` wide; ``bin_px`` is the lower edge, positive
    inside the polygon. Only matched persons count: a person the detector
    missed is wrong for every rule alike, which would hide the rule differences
    (detector misses are reported as ``fn_missed_detection`` instead).
    """
    edges = range(-BOUNDARY_RANGE_PX, BOUNDARY_RANGE_PX, BOUNDARY_BIN_PX)
    stats = {e: {"n": 0, **{r: 0 for r in rules}} for e in edges}
    for zf in frames:
        if not zf.gt_distance_px:
            continue
        by_gt = {g: d for d, g in zf.match.pairs}
        pos = {rule: zf.counted_hits(rule) for rule in rules}
        for g, dist in enumerate(zf.gt_distance_px):
            d = by_gt.get(g)
            if d is None:
                continue
            clipped = min(max(dist, -BOUNDARY_RANGE_PX), BOUNDARY_RANGE_PX - 1e-9)
            edge = int(math.floor(clipped / BOUNDARY_BIN_PX) * BOUNDARY_BIN_PX)
            truth = zf.in_zone[g] == 1
            stats[edge]["n"] += 1
            for r in rules:
                stats[edge][r] += int((d in pos[r]) != truth)
    out = []
    for edge, s in stats.items():
        if s["n"] == 0:
            continue
        out.append({"bin_px": edge, "n": s["n"], "error_rate": {r: s[r] / s["n"] for r in rules}})
    return out


# --- frame and event level ---------------------------------------------------------


def frame_occupancy(frames: Sequence[ZoneFrame], rule: str) -> dict[str, float]:
    """Frame-level: ``pred = counts[rule] >= 1``, ``truth = any GT in_zone``."""
    tp = fp = fn = tn = 0
    for zf in frames:
        pred = len(zf.counted_hits(rule)) >= 1
        truth = any(v == 1 for v in zf.in_zone)
        tp += pred and truth
        fp += pred and not truth
        fn += truth and not pred
        tn += not pred and not truth
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, **rates(tp, fp, fn, tn)}


def entered_frames(
    counts: Sequence[int], frames: Sequence[int], fps: float, enter_frames: int, exit_frames: int
) -> list[int]:
    """Frame numbers of ``zone_entered`` events from the live state machine (H.4)."""
    machine = ZoneStateMachine(enter_frames, exit_frames)
    out = []
    for count, frame in zip(counts, frames, strict=True):
        for event in machine.step(int(count), (frame - 1) * 1000.0 / fps):
            if event.type == "zone_entered":
                out.append(frame)
    return out


@dataclass
class EventMatch:
    pairs: list[tuple[int, int]] = field(default_factory=list)  # (pred frame, truth frame)
    n_pred: int = 0
    n_truth: int = 0
    duplicates: int = 0

    def summary(self) -> dict[str, Any]:
        errors = [abs(p - t) for p, t in self.pairs]
        m = len(self.pairs)
        precision = _safe_div(m, self.n_pred)
        recall = _safe_div(m, self.n_truth)
        return {
            "event_precision": precision,
            "event_recall": recall,
            "event_f1": _safe_div(2 * m, self.n_pred + self.n_truth),
            "timing_err_median": float(np.median(errors)) if errors else math.nan,
            "timing_err_p90": float(np.percentile(errors, 90)) if errors else math.nan,
            "duplicate_rate": _safe_div(self.duplicates, self.n_pred),
            "events_pred": self.n_pred,
            "events_truth": self.n_truth,
            "timing_errors": errors,
        }


def match_events(pred: Sequence[int], truth: Sequence[int], tolerance: int) -> EventMatch:
    """Greedy one-to-one matching of ``zone_entered`` frames, nearest first, within ``tolerance``.

    A *duplicate* is a predicted event left unmatched although a truth event
    (already matched to another prediction) lies within the tolerance.
    """
    candidates = sorted(
        (abs(p - t), i, j)
        for i, p in enumerate(pred)
        for j, t in enumerate(truth)
        if abs(p - t) <= tolerance
    )
    used_p: set[int] = set()
    used_t: set[int] = set()
    pairs = []
    for _dist, i, j in candidates:
        if i in used_p or j in used_t:
            continue
        used_p.add(i)
        used_t.add(j)
        pairs.append((pred[i], truth[j]))
    duplicates = sum(
        1
        for i, p in enumerate(pred)
        if i not in used_p and any(abs(p - t) <= tolerance for t in truth)
    )
    return EventMatch(sorted(pairs), len(pred), len(truth), duplicates)


def event_metrics(
    frames: Sequence[ZoneFrame],
    rule: str,
    fps: float,
    enter_frames: int,
    exit_frames: int,
    tolerance: int,
) -> dict[str, Any]:
    """The state machine applied identically to the GT counts and to the rule's counts."""
    numbers = [zf.frame for zf in frames]
    truth_counts = [sum(1 for v in zf.in_zone if v == 1) for zf in frames]
    pred_counts = [len(zf.counted_hits(rule)) for zf in frames]
    truth = entered_frames(truth_counts, numbers, fps, enter_frames, exit_frames)
    pred = entered_frames(pred_counts, numbers, fps, enter_frames, exit_frames)
    return match_events(pred, truth, tolerance).summary()


# --- everything for one zone -------------------------------------------------------


def evaluate_zone(
    frames: Sequence[ZoneFrame],
    rules: Sequence[str],
    *,
    fps: float,
    enter_frames: int,
    exit_frames: int,
    tolerance: int,
    bootstrap_n: int,
    seed: int,
) -> dict[str, Any]:
    """Person, frame and event metrics with CIs per rule, the McNemar matrix and boundary bins."""
    per_rule: dict[str, dict[str, Any]] = {}
    for rule in rules:
        counts = person_counts(frames, rule)
        tp, fp, fn, tn, missed = (int(v) for v in counts.sum(axis=0))
        ci = bootstrap_ci(counts, bootstrap_n, seed)
        per_rule[rule] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "fn_missed_detection": missed,
            **rates(tp, fp, fn, tn),
            "precision_ci_lo": ci["precision"][0],
            "precision_ci_hi": ci["precision"][1],
            "recall_ci_lo": ci["recall"][0],
            "recall_ci_hi": ci["recall"][1],
            "f1_ci_lo": ci["f1"][0],
            "f1_ci_hi": ci["f1"][1],
            **event_metrics(frames, rule, fps, enter_frames, exit_frames, tolerance),
            "frame_level": frame_occupancy(frames, rule),
        }
    return {
        "frames": len(frames),
        "rules": per_rule,
        "mcnemar": mcnemar_matrix(instance_correct(frames, rules), rules),
        "boundary": boundary_bins(frames, rules),
    }


def detector_recall(matches: Sequence[FrameMatch], n_gt: Sequence[int]) -> dict[str, float]:
    """Detector recall and precision at IoU 0.5 over all frames with MOT ground truth."""
    matched = sum(len(m.pairs) for m in matches)
    total_gt = sum(n_gt)
    false_dets = sum(len(m.unmatched_dets) for m in matches)
    return {
        "detector_recall": _safe_div(matched, total_gt),
        "detector_precision": _safe_div(matched, matched + false_dets),
        "gt_persons": total_gt,
        "matched": matched,
    }


def cohens_kappa(a: Sequence[int], b: Sequence[int]) -> float:
    """Cohen's kappa of two annotators' 0/1 labels on the same items."""
    if len(a) != len(b):
        raise ValueError("label lists differ in length")
    n = len(a)
    if n == 0:
        return math.nan
    agree = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    if chance == 1.0:
        return 1.0 if agree == 1.0 else math.nan
    return (agree - chance) / (1 - chance)
