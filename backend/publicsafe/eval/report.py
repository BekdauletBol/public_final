"""``report.md`` and ``figures/`` for one evaluation run (I.6); matplotlib only."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

#: Rules compared with the baseline in the E1 check.
FOOT_RULES = (
    "bottom_center",
    "three_point_any",
    "three_point_majority",
    "three_point_all",
    "bottom_strip",
)
BASELINE = "bbox_overlap"


def fmt(value: Any, nd: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{nd}f}"
    return str(value)


def ci(row: dict[str, Any], name: str) -> str:
    lo, hi = row.get(f"{name}_ci_lo"), row.get(f"{name}_ci_hi")
    if lo is None or hi is None:
        return "-"
    return f"[{lo:.3f}, {hi:.3f}]"


def table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(fmt(c) for c in r) + " |" for r in rows]
    return lines


def matched_recall(row: dict[str, Any]) -> float | None:
    """Recall over detected persons only: TP / (TP + FN - FN missed detection)."""
    den = row["tp"] + row["fn"] - row["fn_missed_detection"]
    return row["tp"] / den if den else None


def e1_check(pooled: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The E1 criterion on one data source: precision gain, CI separation, recall loss."""
    base = pooled.get(BASELINE)
    if base is None:
        return []
    out = []
    for rule in FOOT_RULES:
        row = pooled.get(rule)
        if row is None or row.get("precision") is None or base.get("precision") is None:
            continue
        gain = row["precision"] - base["precision"]
        separated = (
            row.get("precision_ci_lo") is not None
            and base.get("precision_ci_hi") is not None
            and row["precision_ci_lo"] > base["precision_ci_hi"]
        )
        loss = (base.get("recall") or 0.0) - (row.get("recall") or 0.0)
        out.append(
            {
                "rule": rule,
                "precision_gain": gain,
                "ci_separated": separated,
                "recall_loss": loss,
                "meets": gain >= 0.10 and separated and loss <= 0.05,
            }
        )
    return out


def e3_check(sweep_pooled: Sequence[dict[str, Any]], rule: str) -> dict[str, Any] | None:
    """Duplicate-rate drop from no hysteresis (1 in / 1 out) to 3 in / 5 out (E3)."""
    by = {(r["enter_frames"], r["exit_frames"]): r for r in sweep_pooled if r["rule"] == rule}
    none, hyst = by.get((1, 1)), by.get((3, 5))
    if none is None or hyst is None:
        return None
    d0, d1 = none.get("duplicate_rate"), hyst.get("duplicate_rate")
    drop = None if not d0 or d1 is None else (d0 - d1) / d0
    return {"rule": rule, "dup_none": d0, "dup_hyst": d1, "drop": drop}


def write_report(run_dir: Path, doc: dict[str, Any], figures: bool = True) -> Path:
    run = doc["run"]
    cfg = doc["config"]
    results = doc["results"]
    pooled = {r["rule"]: r for r in results if r["sequence"] == "ALL"}
    rules = cfg["rules"]
    lines: list[str] = [f"# Evaluation report {run['run_id']}", ""]
    if doc.get("derived_gt"):
        lines += [
            "> **Sanity check only.** At least one zone uses `source=derived` ground truth",
            "> (MOT17 GT box bottom-centre inside the polygon). That definition is the",
            "> `bottom_center` rule applied to the GT box, so it favours the foot-point",
            "> rules and must not be used for the headline comparison (spec I.3).",
            "",
        ]
    lines += table(
        ["Item", "Value"],
        [
            ["Started (UTC)", run["started_at"]],
            ["Host / platform", f"{run['host']} / {run['platform']}"],
            ["Device", run.get("device")],
            ["Model SHA-256", run.get("model_sha256")],
            ["Git commit", run.get("git_commit")],
            ["Config SHA-256", run["config_sha256"]],
            ["Sequences", ", ".join(cfg["sequences"])],
            ["Detector", f"conf {cfg['detector']['conf']}, imgsz {cfg['detector']['imgsz']}"],
            [
                "Matching",
                f"IoU {cfg['matching']['iou']}, classes {cfg['matching']['gt_classes']}, "
                f"min visibility {cfg['matching']['min_visibility']}",
            ],
            [
                "State machine / tolerance",
                f"{cfg['state_machine']['enter_frames']} in / "
                f"{cfg['state_machine']['exit_frames']} out, "
                f"±{cfg['events']['tolerance_frames']} frames",
            ],
            ["Bootstrap", f"{cfg['stats']['bootstrap_n']} resamples, seed {cfg['stats']['seed']}"],
            ["Invariant violations", doc.get("invariant_violations", 0)],
            ["GT persons without a label", doc.get("missing_labels", 0)],
        ],
    )

    lines += ["", "## Per-person membership, pooled over all sequences and zones", ""]
    lines += table(
        ["Rule", "Precision [95 % CI]", "Recall [95 % CI]", "F1 [95 % CI]", "FPR",
         "Recall (matched)", "TP", "FP", "FN", "FN missed det.", "Event P", "Event R",
         "Event F1", "Dup. rate"],
        [
            [
                r,
                f"{fmt(pooled[r]['precision'])} {ci(pooled[r], 'precision')}",
                f"{fmt(pooled[r]['recall'])} {ci(pooled[r], 'recall')}",
                f"{fmt(pooled[r]['f1'])} {ci(pooled[r], 'f1')}",
                pooled[r]["fpr"],
                matched_recall(pooled[r]),
                pooled[r]["tp"],
                pooled[r]["fp"],
                pooled[r]["fn"],
                pooled[r]["fn_missed_detection"],
                pooled[r].get("event_precision"),
                pooled[r].get("event_recall"),
                pooled[r].get("event_f1"),
                pooled[r].get("duplicate_rate"),
            ]
            for r in rules
            if r in pooled
        ],
    )  # fmt: skip
    lines += [
        "",
        "Recall counts persons the detector missed as FN (`FN missed det.`); *Recall (matched)*",
        "leaves them out, so it isolates the rule from the detector.",
    ]

    check = e1_check(pooled)
    if check:
        lines += ["", "### E1 check on this data source", ""]
        lines += table(
            ["Rule", "Precision gain vs bbox_overlap", "CIs separated", "Recall loss", "Meets E1"],
            [[c["rule"], c["precision_gain"], c["ci_separated"], c["recall_loss"], c["meets"]]
             for c in check],
        )  # fmt: skip
        lines += [
            "",
            "E1 needs the criterion on at least 2 of 3 data sources (MOT17, WILDTRACK, own",
            "recordings); this table covers one.",
        ]

    lines += ["", "## Per sequence and zone (precision / recall / F1)", ""]
    zone_rows = [r for r in results if r["sequence"] != "ALL"]
    keys = sorted({(r["sequence"], r["zone"]) for r in zone_rows})
    lines += table(
        ["Sequence", "Zone", *rules],
        [
            [
                seq,
                zone,
                *[
                    next(
                        (
                            f"{fmt(r['precision'])} / {fmt(r['recall'])} / {fmt(r['f1'])}"
                            for r in zone_rows
                            if r["sequence"] == seq and r["zone"] == zone and r["rule"] == rule
                        ),
                        "-",
                    )
                    for rule in rules
                ],
            ]
            for seq, zone in keys
        ],
    )

    lines += ["", "## Detector (YOLOv8n) against MOT17 boxes at IoU 0.5", ""]
    lines += table(
        ["Sequence", "Frames", "GT persons", "Matched", "Recall", "Precision"],
        [
            [name, s["frames"], s["detector"]["gt_persons"], s["detector"]["matched"],
             s["detector"]["detector_recall"], s["detector"]["detector_precision"]]
            for name, s in doc["sequences"].items()
        ],
    )  # fmt: skip

    lines += ["", "## Frame-level occupancy, pooled", ""]
    frame_rows = []
    for rule in rules:
        tp = fp = fn = tn = 0
        for s in doc["sequences"].values():
            for z in s["zones"].values():
                fl = z["frame_level"][rule]
                tp, fp, fn, tn = tp + fl["tp"], fp + fl["fp"], fn + fl["fn"], tn + fl["tn"]
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        frame_rows.append([rule, tp, fp, fn, tn, p, r])
    lines += table(["Rule", "TP", "FP", "FN", "TN", "Precision", "Recall"], frame_rows)

    lines += [
        "",
        "## McNemar exact test, pooled (p-value; b = row right & column wrong, c = reverse)",
        "",
    ]
    mc = doc["mcnemar_pooled"]
    lines += table(
        ["", *rules],
        [
            [a, *("—" if a == b else f"{mc[a][b]['p']:.2g} ({mc[a][b]['b']}/{mc[a][b]['c']})"
                  for b in rules)]
            for a in rules
        ],
    )  # fmt: skip

    lines += ["", "## Boundary analysis, pooled (error rate by GT bottom-centre distance, px)", ""]
    lines += [
        "Matched persons only. Positive distance = inside the zone; the outermost bins",
        "include everything beyond ±100 px.",
        "",
    ]
    lines += table(
        ["Bin (px)", "n", *rules],
        [[b["bin_px"], b["n"], *[b["error_rate"][r] for r in rules]]
         for b in doc["boundary_pooled"]],
    )  # fmt: skip

    if doc.get("sweep_pooled"):
        lines += ["", "## Hysteresis sweep (E3), pooled event metrics", ""]
        lines += table(
            ["Rule", "Enter", "Exit", "Pred events", "Truth events", "Event P", "Event R",
             "Event F1", "Dup. rate"],
            [[r["rule"], r["enter_frames"], r["exit_frames"], r["events_pred"], r["events_truth"],
              r["event_precision"], r["event_recall"], r["event_f1"], r["duplicate_rate"]]
             for r in doc["sweep_pooled"]],
        )  # fmt: skip
        e3 = [x for x in (e3_check(doc["sweep_pooled"], r) for r in rules) if x]
        if e3:
            lines += ["", "Duplicate-rate drop from 1 in / 1 out to 3 in / 5 out:", ""]
            lines += table(
                ["Rule", "Dup. rate 1/1", "Dup. rate 3/5", "Relative drop"],
                [[x["rule"], x["dup_none"], x["dup_hyst"], x["drop"]] for x in e3],
            )

    t = doc["timings"]
    lines += ["", "## Offline latency per frame (ms)", ""]
    lines += table(
        ["Span", "p50", "p90", "p95", "p99", "mean", "frames"],
        [[name, *(t[name].get(k) for k in ("p50", "p90", "p95", "p99", "mean", "n"))]
         for name in ("decode_ms", "infer_ms", "rules_ms", "total_ms")],
    )  # fmt: skip
    lines += [
        "",
        "Offline spans exclude the network, the mailbox wait and the dashboard fan-out;",
        "live latency is measured by the load test (E5) and the metrics panel (E6).",
    ]

    if figures:
        names = write_figures(run_dir / "figures", doc, pooled, rules)
        if names:
            lines += ["", "## Figures", ""]
            lines += [f"![{n}](figures/{n})" for n in names]

    path = run_dir / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_figures(
    folder: Path, doc: dict[str, Any], pooled: dict[str, dict[str, Any]], rules: Sequence[str]
) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    folder.mkdir(parents=True, exist_ok=True)
    names: list[str] = []

    present = [r for r in rules if r in pooled and pooled[r]["precision"] is not None]
    if present:
        fig, ax = plt.subplots(figsize=(8, 4))
        x = range(len(present))
        for offset, metric, color in ((-0.2, "precision", "#3b6ea8"), (0.2, "recall", "#d98a2b")):
            vals = [pooled[r][metric] or 0.0 for r in present]
            lo = [
                v - (pooled[r][f"{metric}_ci_lo"] or v) for v, r in zip(vals, present, strict=True)
            ]
            hi = [
                (pooled[r][f"{metric}_ci_hi"] or v) - v for v, r in zip(vals, present, strict=True)
            ]
            ax.bar([i + offset for i in x], vals, 0.4, yerr=[lo, hi], label=metric, color=color,
                   capsize=3)  # fmt: skip
        ax.set_xticks(list(x), present, rotation=20, ha="right")
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("pooled value (95 % CI)")
        ax.legend()
        ax.set_title("Precision and recall per rule")
        fig.tight_layout()
        fig.savefig(folder / "pr_bars.png", dpi=120)
        plt.close(fig)
        names.append("pr_bars.png")

    bins = doc.get("boundary_pooled") or []
    if bins:
        fig, ax = plt.subplots(figsize=(8, 4))
        xs = [b["bin_px"] + 5 for b in bins]
        for rule in rules:
            ax.plot(xs, [b["error_rate"][rule] for b in bins], marker="o", ms=3, label=rule)
        ax.axvline(0, color="grey", lw=0.8, ls="--")
        ax.set_xlabel("GT bottom-centre distance to the zone edge (px, + = inside)")
        ax.set_ylabel("error rate")
        ax.legend(fontsize=8)
        ax.set_title("Errors near the zone boundary")
        fig.tight_layout()
        fig.savefig(folder / "boundary.png", dpi=120)
        plt.close(fig)
        names.append("boundary.png")

    active = doc["config"]["active_rule"]
    errors = [
        e
        for s in doc["sequences"].values()
        for z in s["zones"].values()
        for e in (z["events"].get(active, {}).get("timing_errors") or [])
    ]
    if errors:
        fig, ax = plt.subplots(figsize=(6, 3.5))
        ax.hist(errors, bins=range(0, max(errors) + 2), color="#3b6ea8")
        ax.set_xlabel("|predicted - true| zone_entered frame")
        ax.set_ylabel("events")
        ax.set_title(f"Event timing error ({active})")
        fig.tight_layout()
        fig.savefig(folder / "event_timing.png", dpi=120)
        plt.close(fig)
        names.append("event_timing.png")
    return names
