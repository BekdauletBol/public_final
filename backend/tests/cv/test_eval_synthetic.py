"""M5: ``eval run`` on a 100-frame synthetic sequence reproduces hand-computed numbers.

Frame 200 x 100 px, zone = band x 50..150, y 30..60 (normalised 0.25..0.75, 0.3..0.6).

| Who | Frames | Box (x1, y1, x2, y2) | GT in_zone | Detected |
|---|---|---|---|---|
| P1, feet in the zone | 1-100 | 60, 10, 90, 50 | 1 | frames 1-90 only |
| P2, right of the zone | 1-100 | 160, 10, 190, 50 | 0 | every frame |
| P3, body over the zone, feet below | 51-100 | 100, 40, 130, 90 | 0 | every frame |
| false detection in the zone | 21-25 | 120, 20, 140, 55 | - | every frame |
| static person (class 7, ignore) | 1-100 | 50, 20, 70, 55 | - | every frame |

By hand, per person: P1 gives TP 90 and FN 10 (missed) for every rule; P2 gives
TN 100; P3 gives FP 50 for ``bbox_overlap`` and TN 50 for the others; the false
detection gives FP 5 for every rule; the ignore-region detection is dropped.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml
from typer.testing import CliRunner

from publicsafe.cli import app
from publicsafe.core.detector import FakeDetector
from publicsafe.eval.runner import InvariantError, check_per_frame, run

W, H, N = 200, 100, 100
ZONE = [[0.25, 0.3], [0.75, 0.3], [0.75, 0.6], [0.25, 0.6]]
P1 = (60, 10, 90, 50)
P2 = (160, 10, 190, 50)
P3 = (100, 40, 130, 90)
FALSE_DET = (120, 20, 140, 55)
STATIC = (50, 20, 70, 55)
FOOT_RULES = (
    "bottom_center",
    "three_point_any",
    "three_point_majority",
    "three_point_all",
    "bottom_strip",
)


def present(frame: int) -> list[tuple[int, tuple[int, int, int, int], int, int]]:
    """(person_id, box, flag, class) of every MOT row on ``frame``."""
    rows = [(1, P1, 1, 1), (2, P2, 1, 1), (9, STATIC, 0, 7)]
    if frame > 50:
        rows.append((3, P3, 1, 1))
    return rows


def detections(frame: int) -> list[dict]:
    dets = []
    if frame <= 90:
        dets.append({"bbox": list(P1), "conf": 0.9})
    dets.append({"bbox": list(P2), "conf": 0.9})
    if frame > 50:
        dets.append({"bbox": list(P3), "conf": 0.8})
    if 21 <= frame <= 25:
        dets.append({"bbox": list(FALSE_DET), "conf": 0.6})
    dets.append({"bbox": list(STATIC), "conf": 0.7})
    return dets


def build_sequence(root: Path) -> Path:
    seq = root / "SYN-01"
    (seq / "img1").mkdir(parents=True)
    (seq / "gt").mkdir()
    (seq / "seqinfo.ini").write_text(
        "[Sequence]\nname=SYN-01\nimDir=img1\nframeRate=30\n"
        f"seqLength={N}\nimWidth={W}\nimHeight={H}\nimExt=.jpg\n"
    )
    ok, buf = cv2.imencode(".jpg", np.full((H, W, 3), 90, np.uint8))
    assert ok
    for f in range(1, N + 1):
        (seq / "img1" / f"{f:06d}.jpg").write_bytes(buf.tobytes())
    lines = []
    for f in range(1, N + 1):
        for pid, (x1, y1, x2, y2), flag, cls in present(f):
            lines.append(f"{f},{pid},{x1 + 1},{y1 + 1},{x2 - x1},{y2 - y1},{flag},{cls},1.0")
    (seq / "gt" / "gt.txt").write_text("\n".join(lines) + "\n")
    return seq


def build_run(
    root: Path,
    *,
    assert_invariants: bool = True,
    every: int = 1,
    unlabelled: tuple[int, ...] = (),
    zone_names: tuple[str, ...] = ("band",),
) -> Path:
    """``every`` = label every k-th frame; ``unlabelled`` = person ids left out of the GT CSV."""
    seq = build_sequence(root)
    zones = root / "zones.json"
    zones.write_text(
        json.dumps(
            {
                "sequence": "SYN-01",
                "frame_w": W,
                "frame_h": H,
                "zones": [{"name": name, "points": ZONE} for name in zone_names],
            }
        )
    )
    gt_files = {}
    for name in zone_names:
        gt = root / f"SYN-01.{name}.csv"
        gt_files[name] = str(gt)
        with gt.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["frame", "person_id", "in_zone", "source", "annotator"])
            for f in range(1, N + 1, every):
                for pid, _box, flag, _cls in present(f):
                    if flag and pid not in unlabelled:
                        w.writerow([f, pid, 1 if pid == 1 else 0, "manual", "test"])
    config = root / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "sequences": [
                    {
                        "name": "SYN-01",
                        "path": str(seq),
                        "fps": 30,
                        "zone_file": str(zones),
                        "gt_files": gt_files,
                    }
                ],
                "rules": "all",
                "state_machine": {"enter_frames": 3, "exit_frames": 5},
                "matching": {"iou": 0.5, "gt_classes": [1], "min_visibility": 0.25},
                "events": {"tolerance_frames": 10},
                "stats": {"bootstrap_n": 300, "seed": 1234},
                "assert_invariants": assert_invariants,
                "output_dir": str(root / "out"),
            }
        )
    )
    return config


def fake_detector() -> FakeDetector:
    return FakeDetector([detections(f) for f in range(1, N + 1)])


@pytest.fixture
def result(tmp_path: Path) -> tuple[Path, dict]:
    config = build_run(tmp_path)
    out = run(config, detector=fake_detector(), sweep=[(1, 1), (3, 5)], run_id="syn", figures=True)
    return out.run_dir, out.metrics


def row(metrics: dict, rule: str, sequence: str = "SYN-01") -> dict:
    return next(r for r in metrics["results"] if r["sequence"] == sequence and r["rule"] == rule)


def test_person_level_counts_match_hand_computation(result: tuple[Path, dict]) -> None:
    _, metrics = result
    bbox = row(metrics, "bbox_overlap")
    assert (bbox["tp"], bbox["fp"], bbox["fn"], bbox["tn"], bbox["fn_missed_detection"]) == (
        90, 55, 10, 100, 10,
    )  # fmt: skip
    assert bbox["precision"] == pytest.approx(90 / 145)
    assert bbox["recall"] == pytest.approx(0.9)
    assert bbox["f1"] == pytest.approx(180 / 245)
    assert bbox["fpr"] == pytest.approx(55 / 155)
    for rule in FOOT_RULES:
        r = row(metrics, rule)
        assert (r["tp"], r["fp"], r["fn"], r["tn"], r["fn_missed_detection"]) == (
            90,
            5,
            10,
            150,
            10,
        )
        assert r["precision"] == pytest.approx(90 / 95)
        assert r["f1"] == pytest.approx(180 / 195)
        assert r["fpr"] == pytest.approx(5 / 155)
        assert r["precision_ci_lo"] <= r["precision"] <= r["precision_ci_hi"]
    assert row(metrics, "bbox_overlap", "ALL")["tp"] == 90  # one sequence, one zone: pooled = same


def test_detector_frame_and_event_levels(result: tuple[Path, dict]) -> None:
    _, metrics = result
    det = metrics["sequences"]["SYN-01"]["detector"]
    assert det["gt_persons"] == 250 and det["matched"] == 240
    assert det["detector_recall"] == pytest.approx(0.96)
    assert det["detector_precision"] == pytest.approx(240 / 245)

    frame_level = metrics["sequences"]["SYN-01"]["zones"]["band"]["frame_level"]
    assert (frame_level["bbox_overlap"]["tp"], frame_level["bbox_overlap"]["fn"]) == (100, 0)
    assert (frame_level["bottom_center"]["tp"], frame_level["bottom_center"]["fn"]) == (90, 10)

    for rule in ("bbox_overlap", *FOOT_RULES):  # truth and prediction both enter at frame 3
        r = row(metrics, rule)
        assert (r["event_precision"], r["event_recall"], r["timing_err_median"]) == (1.0, 1.0, 0.0)


def test_mcnemar_and_boundary(result: tuple[Path, dict]) -> None:
    _, metrics = result
    mc = metrics["sequences"]["SYN-01"]["zones"]["band"]["mcnemar"]
    assert (mc["bbox_overlap"]["bottom_center"]["b"], mc["bbox_overlap"]["bottom_center"]["c"]) == (
        0,
        50,
    )
    assert mc["bbox_overlap"]["bottom_center"]["p"] == pytest.approx(2 * 0.5**50)
    assert mc["bottom_center"]["three_point_any"] == {"b": 0, "c": 0, "p": 1.0}

    bins = {b["bin_px"]: b for b in metrics["boundary_pooled"]}
    assert bins[10]["n"] == 90  # P1's feet are 10 px above the zone's lower edge (inside)
    assert bins[10]["error_rate"]["three_point_all"] == 0.0
    assert bins[-30]["n"] == 150  # P2 (25 px right) and P3 (30 px below), matched every frame
    assert bins[-30]["error_rate"]["bbox_overlap"] == pytest.approx(50 / 150)
    assert bins[-30]["error_rate"]["bottom_center"] == 0.0


def test_outputs_written(result: tuple[Path, dict]) -> None:
    run_dir, _ = result
    for name in (
        "config.yaml",
        "run.json",
        "per_frame.jsonl",
        "results.csv",
        "metrics.json",
        "report.md",
    ):
        assert (run_dir / name).is_file(), name
    assert {p.name for p in (run_dir / "figures").iterdir()} >= {"pr_bars.png", "boundary.png"}
    header = (run_dir / "results.csv").read_text().splitlines()[0].split(",")
    assert header[:3] == ["sequence", "zone", "rule"] and header[-1] == "detector_recall"
    lines = (run_dir / "per_frame.jsonl").read_text().splitlines()
    assert len(lines) == N
    first = json.loads(lines[0])
    assert first["type"] == "frame_result" and first["sequence"] == "SYN-01"
    assert {d["matched_gt_id"] for d in first["detections"]} == {1, 2, None}
    report = (run_dir / "report.md").read_text()
    assert "McNemar" in report and "Hysteresis sweep" in report
    assert "Sanity check only" not in report  # manual GT
    assert check_per_frame(run_dir / "per_frame.jsonl") == []


def test_corrupted_per_frame_is_caught(result: tuple[Path, dict], tmp_path: Path) -> None:
    run_dir, _ = result
    lines = (run_dir / "per_frame.jsonl").read_text().splitlines()
    bad = json.loads(lines[60])
    bad["zones"][0]["rule_hits"]["three_point_all"] = [2]  # P3: all three points can't be in
    bad["zones"][0]["counts"]["three_point_all"] = 1
    lines[60] = json.dumps(bad)
    corrupted = tmp_path / "corrupted"
    corrupted.mkdir()
    (corrupted / "per_frame.jsonl").write_text("\n".join(lines) + "\n")
    problems = check_per_frame(corrupted / "per_frame.jsonl")
    assert problems and "three_point_all" in problems[0]
    cli = CliRunner().invoke(app, ["eval", "check", str(corrupted)])
    assert cli.exit_code == 4


def test_assert_invariants_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from publicsafe.eval import runner

    real = runner.compute_all_rules

    def broken(dets, zone):  # type: ignore[no-untyped-def]
        hits, counts = real(dets, zone)
        hits[runner.RuleId.THREE_POINT_ALL] = list(range(len(dets)))  # all ⊆ majority breaks
        return hits, counts

    monkeypatch.setattr(runner, "compute_all_rules", broken)
    with pytest.raises(InvariantError):
        run(build_run(tmp_path), detector=fake_detector(), run_id="broken", figures=False)


def test_derived_gt_is_refused_unless_allowed(tmp_path: Path) -> None:
    config = build_run(tmp_path)
    gt = tmp_path / "SYN-01.band.csv"
    gt.write_text(gt.read_text().replace(",manual,test", ",derived,auto"))
    from publicsafe.eval.runner import ConfigError

    with pytest.raises(ConfigError, match="derived"):
        run(config, detector=fake_detector(), run_id="d1", figures=False)
    raw = yaml.safe_load(config.read_text())
    raw["allow_derived_gt"] = True
    config.write_text(yaml.safe_dump(raw))
    out = run(config, detector=fake_detector(), run_id="d2", figures=False)
    assert "Sanity check only" in (out.run_dir / "report.md").read_text()


# --- regressions from the review of the first MOT17 run ------------------------------


def test_unlabelled_person_is_ignored_not_counted_as_outside(tmp_path: Path) -> None:
    out = run(
        build_run(tmp_path, unlabelled=(3,)), detector=fake_detector(), run_id="u", figures=False
    )
    bbox = row(out.metrics, "bbox_overlap")
    # P3 (feet below the zone) has no label: its 50 detections are neither FP nor TN.
    assert (bbox["tp"], bbox["fp"], bbox["fn"], bbox["tn"]) == (90, 5, 10, 100)
    assert (row(out.metrics, "bottom_center")["tn"]) == 100
    assert out.metrics["missing_labels"] == 50


def test_sparse_labels_keep_events_in_video_frames(tmp_path: Path) -> None:
    # Labels on frames 1 and 62 only (61 frames apart > the 2 s gap reset at 30 fps):
    # the state machine still runs on every video frame, so both enter at frame 3.
    out = run(build_run(tmp_path, every=61), detector=fake_detector(), run_id="s", figures=False)
    r = row(out.metrics, "bottom_center")
    assert (r["events_pred"], r["events_truth"]) == (1, 1)
    assert (r["event_precision"], r["event_recall"], r["timing_err_median"]) == (1.0, 1.0, 0.0)
    assert out.metrics["sequences"]["SYN-01"]["zones"]["band"]["timeline_frames"] == 62
    # Person level uses the two labelled frames only: P1 (TP) and P2 (TN) on both, P3 on 62 (TN).
    assert (r["tp"], r["tn"]) == (2, 3)


def test_pooled_bootstrap_resamples_frames_not_zone_copies(tmp_path: Path) -> None:
    config = build_run(tmp_path, zone_names=("band", "copy"))
    out = run(config, detector=fake_detector(), run_id="p", figures=False)
    one = row(out.metrics, "bbox_overlap")
    pooled = row(out.metrics, "bbox_overlap", "ALL")
    assert pooled["tp"] == 2 * one["tp"]
    # Two identical zones carry no extra information: the CI must not shrink.
    assert pooled["precision_ci_lo"] == pytest.approx(one["precision_ci_lo"])
    assert pooled["precision_ci_hi"] == pytest.approx(one["precision_ci_hi"])


def test_pooled_rows_and_sweep_report_timing(result: tuple[Path, dict]) -> None:
    run_dir, metrics = result
    pooled = row(metrics, "bottom_center", "ALL")
    assert (pooled["timing_err_median"], pooled["timing_err_p90"]) == (0.0, 0.0)
    hyst = next(
        r
        for r in metrics["sweep_pooled"]
        if r["rule"] == "bottom_center" and (r["enter_frames"], r["exit_frames"]) == (3, 5)
    )
    assert hyst["timing_err_p90"] == 0.0
    assert "Meets E3" in (run_dir / "report.md").read_text()


def test_duplicate_sequence_names_are_rejected(tmp_path: Path) -> None:
    from publicsafe.eval.runner import ConfigError

    config = build_run(tmp_path)
    raw = yaml.safe_load(config.read_text())
    raw["sequences"].append(dict(raw["sequences"][0]))
    config.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="unique"):
        run(config, detector=fake_detector(), run_id="dup", figures=False)
