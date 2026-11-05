"""``publicsafe eval run``: detector + all rules + the state machine on a sequence (F-EVAL-1, I.5a).

The run is in two passes. The first pass decodes every frame, runs the
detector and all six rules on every zone, matches detections to the MOT boxes
and streams ``per_frame.jsonl``. The second pass computes the metrics from the
stored per-frame results, so hysteresis sweeps (E3) reuse the detections
instead of running the model again.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import itertools
import json
import math
import platform
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from publicsafe.config import ROOT_DIR
from publicsafe.core.detector import Detector
from publicsafe.core.models import Detection
from publicsafe.core.rules import ALL_RULES, DEFAULT_RULE, RuleId, ZonePolygon, compute_all_rules
from publicsafe.core.rules import check_invariants as rule_invariants
from publicsafe.core.state_machine import ZoneStateMachine
from publicsafe.eval import metrics as m
from publicsafe.eval.datasets import (
    DatasetError,
    GtBox,
    ZoneFile,
    ZoneGroundTruth,
    load_zone_file,
    open_source,
    read_gt_csv,
    read_mot_gt,
    split_gt,
)
from publicsafe.worker.pipeline import decode_jpeg

RESULT_COLUMNS = (
    "sequence",
    "zone",
    "rule",
    "tp",
    "fp",
    "fn",
    "tn",
    "fn_missed_detection",
    "precision",
    "recall",
    "f1",
    "fpr",
    "precision_ci_lo",
    "precision_ci_hi",
    "recall_ci_lo",
    "recall_ci_hi",
    "f1_ci_lo",
    "f1_ci_hi",
    "event_precision",
    "event_recall",
    "event_f1",
    "timing_err_median",
    "timing_err_p90",
    "duplicate_rate",
    "detector_recall",
)
#: ``sequence`` / ``zone`` value of the rows pooled over every sequence and zone.
POOLED = "ALL"
PACKAGES = ("ultralytics", "torch", "numpy", "opencv-python-headless", "shapely", "scipy")


class ConfigError(ValueError):
    """The evaluation config is invalid (message names the key)."""


class InvariantError(RuntimeError):
    """``assert_invariants`` found a rule result violating the subset chain (E2)."""


# --- config ------------------------------------------------------------------------


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT_DIR / p


def _take(raw: dict[str, Any], allowed: dict[str, Any], where: str) -> dict[str, Any]:
    unknown = set(raw) - set(allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    return {**allowed, **raw}


@dataclass
class SequenceConfig:
    name: str
    path: Path
    zone_file: Path
    gt_files: dict[str, Path]
    fps: float | None = None
    gt_boxes: Path | None = None  # MOT gt.txt; default <path>/gt/gt.txt


@dataclass
class EvalConfig:
    sequences: list[SequenceConfig]
    detector: dict[str, Any]
    rules: list[str]
    enter_frames: int = 3
    exit_frames: int = 5
    iou: float = 0.5
    gt_classes: list[int] = field(default_factory=lambda: [1])
    min_visibility: float = 0.25
    tolerance_frames: int = 10
    bootstrap_n: int = 5000
    seed: int = 1234
    assert_invariants: bool = True
    allow_derived_gt: bool = False
    active_rule: str = DEFAULT_RULE.value
    max_frames: int | None = None
    output_dir: Path = ROOT_DIR / "data" / "eval"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EvalConfig:
        top = _take(
            raw,
            {
                "sequences": None,
                "detector": {},
                "rules": "all",
                "state_machine": {},
                "matching": {},
                "events": {},
                "stats": {},
                "assert_invariants": True,
                "allow_derived_gt": False,
                "active_rule": DEFAULT_RULE.value,
                "max_frames": None,
                "output_dir": "data/eval",
            },
            "config",
        )
        if not top["sequences"]:
            raise ConfigError("config: `sequences` must list at least one sequence")
        names = [str(seq.get("name")) for seq in top["sequences"]]
        if len(set(names)) != len(names):
            raise ConfigError("config: sequence names must be unique")
        sequences = []
        for i, s in enumerate(top["sequences"]):
            s = _take(
                s,
                {"name": None, "path": None, "fps": None, "zone_file": None,
                 "gt_files": None, "gt_boxes": None},
                f"sequences[{i}]",
            )  # fmt: skip
            for key in ("name", "path", "zone_file", "gt_files"):
                if not s[key]:
                    raise ConfigError(f"sequences[{i}]: `{key}` is required")
            sequences.append(
                SequenceConfig(
                    name=str(s["name"]),
                    path=_resolve(s["path"]),
                    zone_file=_resolve(s["zone_file"]),
                    gt_files={str(k): _resolve(v) for k, v in s["gt_files"].items()},
                    fps=float(s["fps"]) if s["fps"] else None,
                    gt_boxes=_resolve(s["gt_boxes"]) if s["gt_boxes"] else None,
                )
            )
        det = _take(
            top["detector"],
            {"model": "yolov8n.pt", "device": None, "conf": 0.30, "imgsz": 640, "half": False},
            "detector",
        )
        rules = [r.value for r in ALL_RULES] if top["rules"] == "all" else list(top["rules"])
        for r in rules:
            if r not in {x.value for x in ALL_RULES}:
                raise ConfigError(f"rules: unknown rule {r!r}")
        sm = _take(top["state_machine"], {"enter_frames": 3, "exit_frames": 5}, "state_machine")
        match = _take(
            top["matching"], {"iou": 0.5, "gt_classes": [1], "min_visibility": 0.25}, "matching"
        )
        events = _take(top["events"], {"tolerance_frames": 10}, "events")
        stats = _take(top["stats"], {"bootstrap_n": 5000, "seed": 1234}, "stats")
        if top["active_rule"] not in rules:
            raise ConfigError(f"active_rule {top['active_rule']!r} is not in `rules`")
        return cls(
            sequences=sequences,
            detector=det,
            rules=rules,
            enter_frames=int(sm["enter_frames"]),
            exit_frames=int(sm["exit_frames"]),
            iou=float(match["iou"]),
            gt_classes=[int(c) for c in match["gt_classes"]],
            min_visibility=float(match["min_visibility"]),
            tolerance_frames=int(events["tolerance_frames"]),
            bootstrap_n=int(stats["bootstrap_n"]),
            seed=int(stats["seed"]),
            assert_invariants=bool(top["assert_invariants"]),
            allow_derived_gt=bool(top["allow_derived_gt"]),
            active_rule=str(top["active_rule"]),
            max_frames=int(top["max_frames"]) if top["max_frames"] else None,
            output_dir=_resolve(top["output_dir"]),
        )

    @classmethod
    def load(cls, path: Path) -> EvalConfig:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: expected a mapping at the top level")
        return cls.from_dict(raw)


def parse_sweep(items: Sequence[str]) -> list[tuple[int, int]]:
    """``["enter_frames=1,3,5", "exit_frames=1,5,10"]`` -> every (enter, exit) combination."""
    values: dict[str, list[int]] = {}
    for item in items:
        for part in item.split():
            key, _, raw = part.partition("=")
            if key not in ("enter_frames", "exit_frames") or not raw:
                raise ConfigError(
                    f"--sweep: expected enter_frames=… or exit_frames=…, got {part!r}"
                )
            values[key] = [int(v) for v in raw.split(",") if v.strip()]
    enters = values.get("enter_frames")
    exits = values.get("exit_frames")
    if not enters or not exits:
        raise ConfigError("--sweep needs both enter_frames=… and exit_frames=…")
    return list(itertools.product(enters, exits))


# --- pass 1: detection and rules ----------------------------------------------------


@dataclass
class SequenceData:
    """Everything pass 2 needs about one sequence, kept in memory (a few MB per 1,000 frames)."""

    name: str
    fps: float
    width: int
    height: int
    zones: ZoneFile
    gt: dict[str, ZoneGroundTruth]
    frames: list[int] = field(default_factory=list)
    matches: dict[int, m.FrameMatch] = field(default_factory=dict)
    counted: dict[int, list[GtBox]] = field(default_factory=dict)
    hits: dict[int, dict[str, dict[str, list[int]]]] = field(default_factory=dict)
    timings: dict[str, list[float]] = field(
        default_factory=lambda: {"decode_ms": [], "infer_ms": [], "rules_ms": []}
    )
    invariant_violations: list[str] = field(default_factory=list)
    missing_labels: int = 0


def _signed_distance(poly: ZonePolygon, x: float, y: float) -> float:
    from shapely.geometry import Point

    d = float(poly.polygon.exterior.distance(Point(x, y)))
    return d if poly.covers_point(x, y) else -d


def _detections_json(
    dets: Sequence[Detection], match: m.FrameMatch, ids: Sequence[int]
) -> list[Any]:
    by_det = {d: ids[g] for d, g in match.pairs}
    out = []
    for i, det in enumerate(dets):
        row = det.to_json()
        row["matched_gt_id"] = by_det.get(i)
        row["ignored"] = i in match.ignored_dets
        out.append(row)
    return out


def detect_sequence(
    seq: SequenceConfig,
    cfg: EvalConfig,
    detector: Detector,
    per_frame: Any,
    progress: Callable[[str], None] | None = None,
) -> SequenceData:
    """Pass 1 for one sequence; writes one ``per_frame.jsonl`` line per frame."""
    source = open_source(seq.path, seq.fps)
    zones = load_zone_file(seq.zone_file)
    missing = [z for z in zones.names if z not in seq.gt_files]
    if missing:
        raise ConfigError(f"{seq.name}: no gt_files entry for zone(s) {', '.join(missing)}")
    gt = {name: read_gt_csv(seq.gt_files[name]) for name in zones.names}
    derived = [name for name, g in gt.items() if g.derived_only]
    if derived and not cfg.allow_derived_gt:
        raise ConfigError(
            f"{seq.name}: ground truth for {', '.join(derived)} is source=derived. Derived GT is "
            "for sanity checks only (I.3); set `allow_derived_gt: true` to run anyway."
        )
    gt_path = seq.gt_boxes or seq.path / "gt" / "gt.txt"
    if not gt_path.is_file():
        raise DatasetError(f"{seq.name}: MOT-format boxes {gt_path} not found (needed to match)")
    boxes = read_mot_gt(gt_path)

    data = SequenceData(
        name=seq.name,
        fps=source.fps,
        width=source.width or zones.frame_w,
        height=source.height or zones.frame_h,
        zones=zones,
        gt=gt,
    )
    polygons: dict[tuple[int, int], dict[str, ZonePolygon]] = {}
    machines = {z.name: ZoneStateMachine(cfg.enter_frames, cfg.exit_frames) for z in zones.zones}
    total = len(source) if cfg.max_frames is None else min(cfg.max_frames, len(source))

    for sf in source:
        if cfg.max_frames is not None and sf.index > cfg.max_frames:
            break
        t0 = time.perf_counter()
        image = sf.image if sf.image is not None else decode_jpeg(sf.data or b"")
        t1 = time.perf_counter()
        dets = detector.detect(image)
        t2 = time.perf_counter()
        h, w = image.shape[:2]
        if (w, h) not in polygons:
            polygons[(w, h)] = {z.name: ZonePolygon(z.points, w, h) for z in zones.zones}
        zone_hits: dict[str, dict[str, list[int]]] = {}
        zone_json = []
        for z in zones.zones:
            hits, counts = compute_all_rules(dets, polygons[(w, h)][z.name])
            for problem in rule_invariants(hits):
                data.invariant_violations.append(
                    f"{seq.name} frame {sf.index} zone {z.name}: {problem}"
                )
            zone_hits[z.name] = {rule.value: idx for rule, idx in hits.items()}
            machine = machines[z.name]
            machine.step(counts[RuleId(cfg.active_rule)], sf.t_ms)
            zone_json.append(
                {
                    "zone_id": z.name,
                    "rule_hits": zone_hits[z.name],
                    "counts": {rule.value: n for rule, n in counts.items()},
                    "active_rule": cfg.active_rule,
                    "state": machine.state,
                    "debounced_count": machine.debounced_count,
                }
            )
        t3 = time.perf_counter()
        counted, ignored = split_gt(boxes.get(sf.index, []), cfg.gt_classes, cfg.min_visibility)
        det_arr = np.array([d.bbox_xyxy for d in dets], dtype=float).reshape(-1, 4)
        match = m.match_frame(
            det_arr,
            np.array([b.bbox for b in counted], dtype=float).reshape(-1, 4),
            np.array([b.bbox for b in ignored], dtype=float).reshape(-1, 4),
            cfg.iou,
        )
        ids = [b.person_id for b in counted]
        data.frames.append(sf.index)
        data.matches[sf.index] = match
        data.counted[sf.index] = counted
        data.hits[sf.index] = zone_hits
        data.timings["decode_ms"].append((t1 - t0) * 1000.0)
        data.timings["infer_ms"].append((t2 - t1) * 1000.0)
        data.timings["rules_ms"].append((t3 - t2) * 1000.0)

        line = {
            "type": "frame_result",
            "sequence": seq.name,
            "seq": sf.index,
            "w": w,
            "h": h,
            "detections": _detections_json(dets, match, ids),
            "zones": zone_json,
            "timings": {k: round(v[-1], 2) for k, v in data.timings.items()},
            "gt": [
                {
                    "person_id": b.person_id,
                    "bbox": [round(v, 1) for v in b.bbox],
                    "in_zone": {
                        name: g.labels.get((sf.index, b.person_id))
                        for name, g in gt.items()
                        if sf.index in g.annotated
                    },
                }
                for b in counted
            ],
        }
        per_frame.write(json.dumps(line) + "\n")
        if progress is not None and (sf.index % 100 == 0 or sf.index == total):
            progress(f"{seq.name}: {sf.index}/{total} frames")

    if cfg.assert_invariants and data.invariant_violations:
        raise InvariantError(
            f"{len(data.invariant_violations)} invariant violation(s), first: "
            f"{data.invariant_violations[0]}"
        )
    return data


# --- pass 2: metrics ----------------------------------------------------------------


def zone_frames(data: SequenceData, zone: str) -> list[m.ZoneFrame]:
    """Annotated frames of one zone, with GT labels and boundary distances.

    A counted GT person with no label on an annotated frame (for example
    because the eval's ``min_visibility`` is lower than the annotator's) is
    treated as an ignore region for this zone: neither the person nor the
    detection matched to them is counted. ``missing_labels`` reports how often.
    """
    labels = data.gt[zone]
    poly = ZonePolygon(data.zones.zone(zone).points, data.width, data.height)
    out = []
    for frame in data.frames:
        if frame not in labels.annotated:
            continue
        counted = data.counted[frame]
        match = data.matches[frame]
        in_zone = []
        unlabelled: set[int] = set()
        for g, b in enumerate(counted):
            value = labels.labels.get((frame, b.person_id))
            if value is None:
                unlabelled.add(g)
                value = 0
            in_zone.append(value)
        if unlabelled:
            data.missing_labels += len(unlabelled)
            match = m.FrameMatch(
                pairs=[(d, g) for d, g in match.pairs if g not in unlabelled],
                unmatched_dets=match.unmatched_dets,
                ignored_dets=sorted(
                    [*match.ignored_dets, *(d for d, g in match.pairs if g in unlabelled)]
                ),
                unmatched_gts=[g for g in match.unmatched_gts if g not in unlabelled],
            )
        out.append(
            m.ZoneFrame(
                frame=frame,
                match=match,
                hits=data.hits[frame][zone],
                in_zone=in_zone,
                gt_distance_px=[_signed_distance(poly, *b.bottom_center) for b in counted],
            )
        )
    return out


def zone_timeline(
    data: SequenceData, zone: str, frames: Sequence[m.ZoneFrame], rules: Sequence[str]
) -> m.EventTimeline:
    """Every video frame from the first to the last annotated one, for the event level.

    Predictions come from every frame, as in the live system; the truth count
    is held from the last annotated frame, so sparse labels (``--every k``)
    still give a timeline in video frames for the state machine and tolerance.
    """
    if not frames:
        return m.EventTimeline([], [], {rule: [] for rule in rules})
    annotated = {zf.frame: zf for zf in frames}
    first, last = frames[0].frame, frames[-1].frame
    numbers = [f for f in data.frames if first <= f <= last]
    truth: list[int] = []
    pred: dict[str, list[int]] = {rule: [] for rule in rules}
    current = 0
    for f in numbers:
        zf = annotated.get(f)
        if zf is not None:
            current = sum(1 for v in zf.in_zone if v == 1)
            for rule in rules:
                pred[rule].append(len(zf.counted_hits(rule)))
        else:
            dropped = set(data.matches[f].ignored_dets)
            for rule in rules:
                pred[rule].append(len(set(data.hits[f][zone][rule]) - dropped))
        truth.append(current)
    return m.EventTimeline(numbers, truth, pred)


def _pooled(
    per_zone: dict[tuple[str, str], list[m.ZoneFrame]], cfg: EvalConfig
) -> dict[str, dict[str, Any]]:
    """Rows pooled over every (sequence, zone).

    The bootstrap resamples (sequence, frame) clusters: the zones of one frame
    share its detections, so resampling them separately would make the CIs too
    narrow.
    """
    keys = [(seq, zf.frame) for (seq, _zone), zfs in per_zone.items() for zf in zfs]
    cluster = {key: i for i, key in enumerate(dict.fromkeys(keys))}
    rows_to_cluster = np.array([cluster[k] for k in keys], dtype=np.int64)
    out: dict[str, dict[str, Any]] = {}
    for rule in cfg.rules:
        per_frame = [m.person_counts(zfs, rule) for zfs in per_zone.values()]
        counts = np.concatenate(per_frame) if per_frame else np.zeros((0, 5), np.int64)
        clustered = np.zeros((len(cluster), 5), dtype=np.int64)
        np.add.at(clustered, rows_to_cluster, counts)
        tp, fp, fn, tn, missed = (int(v) for v in counts.sum(axis=0))
        ci = m.bootstrap_ci(clustered, cfg.bootstrap_n, cfg.seed)
        out[rule] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "fn_missed_detection": missed,
            **m.rates(tp, fp, fn, tn),
            "precision_ci_lo": ci["precision"][0],
            "precision_ci_hi": ci["precision"][1],
            "recall_ci_lo": ci["recall"][0],
            "recall_ci_hi": ci["recall"][1],
            "f1_ci_lo": ci["f1"][0],
            "f1_ci_hi": ci["f1"][1],
        }
    return out


def _sum_events(rows: list[dict[str, Any]], timing_errors: Sequence[float]) -> dict[str, float]:
    """Pool event counts over zones (precision/recall from the summed matches)."""
    n_pred = sum(r["events_pred"] for r in rows)
    n_truth = sum(r["events_truth"] for r in rows)
    matched = sum(
        round(r["event_recall"] * r["events_truth"]) if r["events_truth"] else 0 for r in rows
    )
    dups = sum(
        round(r["duplicate_rate"] * r["events_pred"]) if r["events_pred"] else 0 for r in rows
    )
    return {
        "event_precision": m._safe_div(matched, n_pred),
        "event_recall": m._safe_div(matched, n_truth),
        "event_f1": m._safe_div(2 * matched, n_pred + n_truth),
        "timing_err_median": float(np.median(timing_errors)) if timing_errors else math.nan,
        "timing_err_p90": float(np.percentile(timing_errors, 90)) if timing_errors else math.nan,
        "duplicate_rate": m._safe_div(dups, n_pred),
        "events_pred": n_pred,
        "events_truth": n_truth,
    }


def compute_metrics(
    datasets: Sequence[SequenceData], cfg: EvalConfig, sweep: Sequence[tuple[int, int]] = ()
) -> dict[str, Any]:
    """Pass 2: everything that goes into ``results.csv`` and ``metrics.json``."""
    results: list[dict[str, Any]] = []
    detail: dict[str, Any] = {}
    per_zone: dict[tuple[str, str], list[m.ZoneFrame]] = {}
    timing: dict[str, list[float]] = {rule: [] for rule in cfg.rules}
    sweep_rows: list[dict[str, Any]] = []
    sweep_timing: dict[tuple[str, int, int], list[float]] = {}
    for data in datasets:
        det = m.detector_recall(
            [data.matches[f] for f in data.frames], [len(data.counted[f]) for f in data.frames]
        )
        detail[data.name] = {"detector": det, "zones": {}, "frames": len(data.frames)}
        for zone in data.zones.names:
            frames = zone_frames(data, zone)
            timeline = zone_timeline(data, zone, frames, cfg.rules)
            per_zone[(data.name, zone)] = frames
            ev = m.evaluate_zone(
                frames,
                cfg.rules,
                fps=data.fps,
                enter_frames=cfg.enter_frames,
                exit_frames=cfg.exit_frames,
                tolerance=cfg.tolerance_frames,
                bootstrap_n=cfg.bootstrap_n,
                seed=cfg.seed,
                timeline=timeline,
            )
            detail[data.name]["zones"][zone] = {
                "frames": ev["frames"],
                "timeline_frames": len(timeline.frames),
                "gt_sources": sorted(data.gt[zone].sources),
                "annotators": sorted(data.gt[zone].annotators),
                "mcnemar": ev["mcnemar"],
                "boundary": ev["boundary"],
                "frame_level": {r: v["frame_level"] for r, v in ev["rules"].items()},
                "events": {
                    r: {k: v[k] for k in ("events_pred", "events_truth", "timing_errors")}
                    for r, v in ev["rules"].items()
                },
            }
            for rule, v in ev["rules"].items():
                timing[rule].extend(v["timing_errors"])
                results.append(
                    {
                        "sequence": data.name,
                        "zone": zone,
                        "rule": rule,
                        **{k: v[k] for k in RESULT_COLUMNS[3:-1]},
                        "detector_recall": det["detector_recall"],
                        "events_pred": v["events_pred"],
                        "events_truth": v["events_truth"],
                    }
                )
            for enter, exit_ in sweep:
                for rule in cfg.rules:
                    em = m.event_metrics(
                        timeline, rule, data.fps, enter, exit_, cfg.tolerance_frames
                    )
                    sweep_timing.setdefault((rule, enter, exit_), []).extend(
                        em.pop("timing_errors")
                    )
                    sweep_rows.append(
                        {
                            "sequence": data.name,
                            "zone": zone,
                            "rule": rule,
                            "enter_frames": enter,
                            "exit_frames": exit_,
                            **em,
                        }
                    )

    pooled = _pooled(per_zone, cfg)
    all_frames = [zf for zfs in per_zone.values() for zf in zfs]
    all_matches = [d.matches[f] for d in datasets for f in d.frames]
    all_gt = [len(d.counted[f]) for d in datasets for f in d.frames]
    det_all = m.detector_recall(all_matches, all_gt)
    for rule in cfg.rules:
        events = _sum_events([r for r in results if r["rule"] == rule], timing[rule])
        results.append(
            {
                "sequence": POOLED,
                "zone": POOLED,
                "rule": rule,
                **pooled[rule],
                **events,
                "detector_recall": det_all["detector_recall"],
            }
        )
    mcnemar_all = m.mcnemar_matrix(m.instance_correct(all_frames, cfg.rules), cfg.rules)
    boundary_all = m.boundary_bins(all_frames, cfg.rules)

    sweep_pooled = []
    for enter, exit_ in sweep:
        for rule in cfg.rules:
            rows = [
                r
                for r in sweep_rows
                if r["rule"] == rule and r["enter_frames"] == enter and r["exit_frames"] == exit_
            ]
            sweep_pooled.append(
                {
                    "rule": rule,
                    "enter_frames": enter,
                    "exit_frames": exit_,
                    **_sum_events(rows, sweep_timing.get((rule, enter, exit_), [])),
                }
            )

    timings = {
        name: _percentiles([v for d in datasets for v in d.timings[name]])
        for name in ("decode_ms", "infer_ms", "rules_ms")
    }
    timings["total_ms"] = _percentiles(
        [
            sum(vals)
            for d in datasets
            for vals in zip(
                *(d.timings[k] for k in ("decode_ms", "infer_ms", "rules_ms")), strict=True
            )
        ]
    )
    return {
        "results": results,
        "sequences": detail,
        "detector": det_all,
        "mcnemar_pooled": mcnemar_all,
        "boundary_pooled": boundary_all,
        "sweep": sweep_rows,
        "sweep_pooled": sweep_pooled,
        "timings": timings,
        "missing_labels": sum(d.missing_labels for d in datasets),
        "invariant_violations": sum(len(d.invariant_violations) for d in datasets),
        "derived_gt": any(g.derived_only for d in datasets for g in d.gt.values()),
    }


def _percentiles(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {k: math.nan for k in ("p50", "p90", "p95", "p99", "mean")}
    arr = np.asarray(values)
    return {
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "mean": float(arr.mean()),
        "n": len(arr),
    }


# --- outputs ------------------------------------------------------------------------


def _clean(value: Any) -> Any:
    """NaN -> None so metrics.json stays valid JSON."""
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


def write_results_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(RESULT_COLUMNS)
        for row in rows:
            out = []
            for col in RESULT_COLUMNS:
                v = row.get(col)
                if isinstance(v, float):
                    v = "" if math.isnan(v) else f"{v:.4f}"
                out.append(v)
            writer.writerow(out)


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT_DIR, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def make_detector(cfg: EvalConfig) -> Detector:
    from publicsafe.core.detector import UltralyticsDetector

    model = Path(cfg.detector["model"])
    if not model.is_absolute() and not model.exists():
        model = ROOT_DIR / "data" / "models" / model
    detector = UltralyticsDetector(
        model,
        device=cfg.detector["device"],
        conf=float(cfg.detector["conf"]),
        imgsz=int(cfg.detector["imgsz"]),
        half=bool(cfg.detector["half"]),
    )
    detector.warm_up()
    return detector


def seed_everything(seed: int) -> None:
    np.random.seed(seed)
    try:
        import torch
    except ImportError:  # pragma: no cover - torch is a pinned dependency
        return
    torch.manual_seed(seed)


@dataclass
class RunOutput:
    run_dir: Path
    metrics: dict[str, Any]


def run(
    config_path: Path,
    *,
    detector: Detector | None = None,
    sweep: Sequence[tuple[int, int]] = (),
    run_id: str | None = None,
    progress: Callable[[str], None] | None = None,
    figures: bool = True,
) -> RunOutput:
    """Run one configured evaluation and write ``data/eval/<run_id>/`` (I.6)."""
    from publicsafe.eval.report import write_report

    config_bytes = config_path.read_bytes()
    cfg = EvalConfig.load(config_path)
    seed_everything(cfg.seed)
    started = dt.datetime.now(dt.UTC)
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    run_id = run_id or f"{started:%Y%m%dT%H%M%SZ}-{config_sha[:8]}"
    run_dir = cfg.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "config.yaml").write_bytes(config_bytes)

    det = detector if detector is not None else make_detector(cfg)
    datasets = []
    with (run_dir / "per_frame.jsonl").open("w", encoding="utf-8") as per_frame:
        for seq in cfg.sequences:
            datasets.append(detect_sequence(seq, cfg, det, per_frame, progress))
    result = compute_metrics(datasets, cfg, sweep)
    finished = dt.datetime.now(dt.UTC)
    run_info = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "host": platform.node(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "device": getattr(det, "device", None),
        "detector": getattr(det, "name", type(det).__name__),
        "package_versions": _versions(),
        "model_sha256": getattr(det, "model_sha256", None),
        "git_commit": _git_commit(),
        "config_sha256": config_sha,
        "sweep": [list(s) for s in sweep],
    }
    (run_dir / "run.json").write_text(json.dumps(run_info, indent=2) + "\n", encoding="utf-8")
    write_results_csv(run_dir / "results.csv", result["results"])
    metrics_doc = _clean({"run": run_info, "config": _config_summary(cfg), **result})
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics_doc, indent=2) + "\n", encoding="utf-8"
    )
    write_report(run_dir, metrics_doc, figures=figures)
    return RunOutput(run_dir, metrics_doc)


def _config_summary(cfg: EvalConfig) -> dict[str, Any]:
    return {
        "sequences": [s.name for s in cfg.sequences],
        "detector": cfg.detector,
        "rules": cfg.rules,
        "state_machine": {"enter_frames": cfg.enter_frames, "exit_frames": cfg.exit_frames},
        "matching": {
            "iou": cfg.iou,
            "gt_classes": cfg.gt_classes,
            "min_visibility": cfg.min_visibility,
        },
        "events": {"tolerance_frames": cfg.tolerance_frames},
        "stats": {"bootstrap_n": cfg.bootstrap_n, "seed": cfg.seed},
        "assert_invariants": cfg.assert_invariants,
        "allow_derived_gt": cfg.allow_derived_gt,
        "active_rule": cfg.active_rule,
        "max_frames": cfg.max_frames,
    }


# --- re-checking a stored run (E2) -------------------------------------------------


def check_per_frame(path: Path) -> list[str]:
    """Re-check the subset invariants on every line of a ``per_frame.jsonl``."""
    problems: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            row = json.loads(line)
            for zone in row.get("zones", []):
                try:
                    hits = {RuleId(k): v for k, v in zone["rule_hits"].items()}
                except (KeyError, ValueError) as exc:
                    problems.append(f"line {lineno}: malformed rule_hits ({exc})")
                    continue
                if set(hits) != set(ALL_RULES):
                    problems.append(f"line {lineno} zone {zone.get('zone_id')}: missing rules")
                    continue
                for problem in rule_invariants(hits):
                    problems.append(
                        f"line {lineno} {row.get('sequence')} frame {row.get('seq')} "
                        f"zone {zone.get('zone_id')}: {problem}"
                    )
                for rule, idx in zone["rule_hits"].items():
                    if zone.get("counts", {}).get(rule) != len(idx):
                        problems.append(
                            f"line {lineno} zone {zone.get('zone_id')}: count of {rule} "
                            "does not match its hits"
                        )
    return problems
