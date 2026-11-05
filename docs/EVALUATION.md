# Evaluation

This file describes the evaluation harness (M5), the pre-registered
experiments and the results measured so far. Every number below comes from a
run that is committed under [docs/results/](results/) or from a command whose
output is quoted; nothing is copied from the paper.

**Short version.** The harness works end to end on MOT17. On the three MOT17
sequences with *placeholder* zones and *derived* ground truth, the foot-point
rules have about twice the precision of plain box overlap (0.92 against 0.45)
at almost the same recall. Under load, the latest-frame mailbox keeps the p99
queue wait at 67 ms where a FIFO grows to 7.6 s. The headline E1 result still
needs the owner's own zones and manual labels (see
[What is still open](#what-is-still-open)).

## Model

| Item | Value |
|---|---|
| Weights | `yolov8n.pt` (ultralytics release asset, downloaded on first start into `data/models/`) |
| SHA-256 | `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36` |
| Device on the development Mac | `mps` (Apple Silicon, macOS 26.2) |
| Warm-up (first inference) | 3.2 s (M4 Mac), 4.8 s (MacBook Air M1, 2026-11-05) |
| Classes | 0 (person) only; confidence from the camera settings (default 0.30); `imgsz` 640 |

## FP16 on MPS (H.2 measurement, `make test-model`)

Golden image `bus.jpg` (810×1080), 20 inferences each after warm-up:

| Date, machine | Precision | ms / frame | Detections (conf) |
|---|---|---|---|
| 2026-10-10, development Mac | FP32 (`INFERENCE_HALF=false`) | 49.2 | 0.83, 0.85, 0.87 |
| 2026-10-10, development Mac | FP16 (`INFERENCE_HALF=true`) | 57.6 | 0.82, 0.85, 0.87 |
| 2026-11-05, MacBook Air M1 | FP32 | 27.9 | 0.83, 0.85, 0.87 |
| 2026-11-05, MacBook Air M1 | FP16 | 29.6 | 0.82, 0.85, 0.87 |

FP16 was **slower** on both Macs with identical boxes, so `INFERENCE_HALF`
stays `false`. Re-measure with `make test-model` on other hardware before
enabling it.

## Harness

```bash
publicsafe eval fetch mot17                        # MOT17-02, -04, -09 into data/mot17 (git-ignored)
publicsafe eval annotate --sequence data/mot17/train/MOT17-04 \
  --zone-file data/zones/MOT17-04.json --zone strip --every 5 --annotator <name>
publicsafe eval agreement data/gt/a/MOT17-04.strip.csv data/gt/b/MOT17-04.strip.csv
make eval CONFIG=configs/e1_mot17.yaml SWEEP="enter_frames=1,3,5 exit_frames=1,5,10"
publicsafe eval check data/eval/<run_id>           # re-check the rule invariants on a stored run
```

| Piece | What it does |
|---|---|
| `eval fetch mot17` | Reads the central directory of the official `https://motchallenge.net/data/MOT17.zip` and downloads only the train sequences it needs with HTTP range requests (465 MB for three sequences instead of 5.9 GB). Every file is checked against the archive's CRC-32, and each sequence against a SHA-256 manifest recorded after the first download. `--archive MOT17.zip` uses a full local copy instead. |
| Readers | MOT sequences (`seqinfo.ini`, `img1/`, `gt/gt.txt`), image folders, video files and `EVAL_RECORD` folders (`frames.jsonl`, phone timestamps kept). |
| Zones | `data/zones/<sequence>.json` (committed). The three polygons per sequence are **placeholders** copied from the spec example and marked `TODO`; the owner replaces them. |
| Ground truth | `data/gt/<sequence>.<zone>.csv`: `frame, person_id, in_zone, source, annotator`. `eval annotate` writes `source=manual` rows from a local page on `127.0.0.1` (click a box or Tab/Space to toggle, Enter to save). `eval derive-gt` writes `source=derived` rows (GT box bottom-centre inside the polygon). A row with `person_id=0` marks an annotated frame with nobody in it. |
| `eval run` | Runs YOLOv8n and all six rules on every frame, matches detections to the MOT boxes, computes the metrics of spec I.4 and writes `data/eval/<run_id>/{config.yaml, run.json, per_frame.jsonl, results.csv, metrics.json, report.md, figures/}`. |
| Safety rails | Unknown config keys are rejected. Derived GT is refused unless the config says `allow_derived_gt: true`, and then the report starts with a "sanity check only" banner. `assert_invariants: true` stops the run (exit 4) if any frame breaks `all ⊆ majority ⊆ any ⊆ bbox_overlap` or `bottom_center ⊆ any`. |

### Matching and metric definitions (spec I.4)

- Detections are matched to MOT ground truth per frame with the Hungarian
  algorithm on IoU, threshold 0.5. GT rows with `flag=0`, a class other than
  1 (pedestrian) or visibility below 0.25 are ignore regions: a detection that
  is not matched to a counted person but overlaps an ignore region with
  IoU ≥ 0.5 is dropped, not counted as a false positive.
- MOT17 ships no README with the archive, so the `gt.txt` layout (9 columns
  `frame, id, left, top, w, h, flag, class, visibility`, classes 1–12) was
  confirmed against the MOT16 paper (Milan et al., arXiv:1603.00831) and the
  files themselves: in all three sequences `flag=1` occurs only with class 1.
- Per person: a matched pair is TP / FP / FN / TN from `in_zone` and the rule.
  An unmatched detection counts as FP if the rule says "inside", otherwise TN.
  A missed GT person with `in_zone=1` is FN and is also counted in
  `fn_missed_detection`, so detector misses can be told apart from rule
  errors (the report shows "recall (matched)" without them).
- Frame level: predicted occupied if the rule counts ≥ 1 person, truly
  occupied if any GT person is in the zone.
- Event level: the live `ZoneStateMachine` runs on the GT counts and on each
  rule's counts; `zone_entered` events are matched greedily, nearest first,
  within ±10 frames.
- 95 % CIs: bootstrap over frames, 5,000 resamples, seed 1234 (the same
  resamples for every rule of a zone). McNemar: exact binomial test on paired
  per-person outcomes. Boundary analysis: error rate per 10 px bin of the GT
  bottom-centre's signed distance to the zone edge, matched persons only.

The harness itself is tested on a 100-frame synthetic sequence whose numbers
were worked out by hand (`backend/tests/cv/test_eval_synthetic.py`), on a
deliberately corrupted `per_frame.jsonl`, and with an offline mock of the
range-request download.

### Datasets and checksums

| Sequence | Frames | Size | GT persons (class 1, vis ≥ 0.25) | Manifest SHA-256 |
|---|---|---|---|---|
| MOT17-02 (street, static camera) | 600 | 106.6 MB | 9,393 rows | `12c04bd52a35c39e8ff1a0c0e044979e911e682872b85e0147382d052de0c3f3` |
| MOT17-04 (street at night, elevated) | 1,050 | 248.1 MB | 39,821 rows | `ca02ae40d786dc6bb88f436fa28b820a1fb1840cecbddf91c0829df4d145d461` |
| MOT17-09 (shopping street, static) | 525 | 110.8 MB | 3,745 rows | `d171da613f5e4973099e47f8250838f0106fed2072bdc92a51dafa9a33364ede` |

Archive identity when fetched (2026-11-05): 5,860,214,001 bytes,
`Last-Modified: Fri, 18 Dec 2020 17:04:22 GMT`. MOT17 is CC BY-NC-SA 3.0
(<https://motchallenge.net/>); it is never committed. WILDTRACK was not
downloaded: its licence is still not stated on the EPFL page (spec I.1).

## Experiments and pre-registered success criteria

| Id | Question | Command | Output |
|---|---|---|---|
| E1 | Foot-anchored rules vs bbox overlap | `eval run --config configs/e1_mot17.yaml` | per-rule P/R/F1 + CIs, McNemar matrix |
| E2 | Which 3-point combination; invariant check | same run | rule table, invariants asserted (`assert_invariants=true` fails the run if violated) |
| E3 | Hysteresis effect on duplicates | `eval run --sweep enter_frames=1,3,5 exit_frames=1,5,10` | event metrics per setting |
| E4 | Tracking (P1) | `eval run --track` | event metrics with/without |
| E5 | Latest vs FIFO under load | `loadtest --phones 1,2,3,5 --queue latest` and `--queue fifo` | per-camera fps, drop ratio, span percentiles, fairness ratio |
| E6 | Live latency with real phones | metrics export during a 5-minute session per phone count | p50/p90/p95/p99 spans, e2e estimate with offset uncertainty |
| E7 | Difficult conditions | `eval run` on own recordings per condition | per-condition table |

Pre-registered success criteria (copied verbatim from spec I.5, not altered):

| Exp | Success criterion |
|---|---|
| E1 | At least one of `bottom_center` / `three_point_*` has precision ≥ `bbox_overlap` + 0.10 with non-overlapping 95 % CIs and recall loss ≤ 0.05, on ≥ 2 of the 3 data sources (MOT17, WILDTRACK, own recordings) |
| E2 | All rule results reported; invariants `all ⊆ majority ⊆ any ⊆ bbox_overlap` and `bottom_center ⊆ any` hold on every sequence; the active rule is recommended by F1 with the boundary analysis shown |
| E3 | Duplicate-event rate falls by ≥ 50 % with hysteresis (3 in / 5 out) vs none, with event timing error p90 ≤ 10 frames |
| E4 | Event F1 with tracking improves or is unchanged; reported either way |
| E5 | With 3 cameras, p99 queue-wait latency under `latest` is ≥ 30 % lower than under `fifo`; fairness ratio (max ÷ min processed fps) ≤ 1.3 under `latest` |
| E6 | Server-side receive→send p50 reported; end-to-end estimate reported with the RTT uncertainty; ≤ 200 ms p50 is a goal, not a pass/fail gate |
| E7 | Per-condition recall and FPR tabulated; descriptive only |

## Status of each experiment

| Exp | Status | Result so far |
|---|---|---|
| E1 | **Sanity check done, headline open** | Met on MOT17 with derived GT (below). Needs the owner's zones and manual GT, and a second data source. |
| E2 | **Done on MOT17 (derived GT)** | Invariants hold on all 2,175 frames × 3 zones. `bottom_center` has the best F1 (0.738). |
| E3 | **Done on MOT17 (derived GT)** | Duplicate rate 0.114 → 0.000 for `bottom_center` (−100 %); timing error p90 ≤ 5.4 frames in every zone. Few events (23 true events), so this is weak evidence. |
| E4 | Not done | Tracking is a P1 option and is not implemented; `--track` exits with an error instead of being ignored. |
| E5 | **Done** (simulated phones) | p99 queue wait 67 ms (`latest`) vs 7,632 ms (`fifo`), −99 %; fairness 1.00. |
| E6 | Not done | Needs real phones (manual step, see [DEMO_CHECKLIST.md](DEMO_CHECKLIST.md)). Server-side numbers with the real model and simulated phones are below. |
| E7 | Not done | Needs own recordings with consent and MOT-format boxes. |

## Results

### Run `e1-mot17-derived-2026-11-05` (MOT17, placeholder zones, derived GT)

> **Sanity check, not the headline result.** The ground truth here is
> `source=derived`: a person is "in the zone" when the bottom-centre of their
> MOT17 GT box is inside the polygon. That is the `bottom_center` rule applied
> to the GT box, so it favours the foot-point rules by construction. What it
> does test without bias: the detector (YOLOv8n against human-drawn boxes),
> how much box overlap over-reports people whose feet are outside, and the
> whole harness on real data. The zones are the spec's placeholder polygons.

Full report: [docs/results/e1-mot17-derived-2026-11-05/report.md](results/e1-mot17-derived-2026-11-05/report.md)
(`config.yaml`, `run.json`, `results.csv`, `metrics.json` and figures next to
it). Run on a MacBook Air M1 (MPS), git commit `ba6ec50`, model SHA-256 as
above, 2,175 frames, 3 sequences × 3 zones, 5,000 bootstrap resamples.
Reproduce with:

```bash
publicsafe eval fetch mot17
for s in MOT17-02 MOT17-04 MOT17-09; do
  publicsafe eval derive-gt --sequence data/mot17/train/$s --zone-file data/zones/$s.json
done
make eval CONFIG=configs/e1_mot17_derived.yaml SWEEP="enter_frames=1,3,5 exit_frames=1,5,10"
```

Per-person membership, pooled over all sequences and zones:

| Rule | Precision [95 % CI] | Recall [95 % CI] | F1 [95 % CI] | FPR | Recall (matched) | Event F1 |
|---|---|---|---|---|---|---|
| `bbox_overlap` | 0.454 [0.446, 0.462] | 0.635 [0.622, 0.649] | 0.530 [0.522, 0.537] | 0.204 | 0.983 | 0.378 |
| `bottom_center` | **0.920** [0.915, 0.925] | 0.616 [0.604, 0.630] | **0.738** [0.728, 0.748] | 0.014 | 0.953 | **0.680** |
| `three_point_any` | 0.804 [0.797, 0.810] | 0.620 [0.607, 0.633] | 0.700 [0.691, 0.709] | 0.040 | 0.959 | 0.571 |
| `three_point_majority` | 0.921 [0.916, 0.926] | 0.615 [0.602, 0.628] | 0.737 [0.728, 0.747] | 0.014 | 0.951 | 0.596 |
| `three_point_all` | 0.934 [0.929, 0.938] | 0.563 [0.552, 0.576] | 0.703 [0.693, 0.712] | 0.011 | 0.872 | 0.604 |
| `bottom_strip` | 0.879 [0.874, 0.885] | 0.613 [0.601, 0.627] | 0.723 [0.714, 0.732] | 0.022 | 0.949 | 0.571 |

E1 check on this one data source (precision gain over `bbox_overlap`, CIs
separated, recall loss ≤ 0.05):

| Rule | Precision gain | CIs separated | Recall loss | Meets E1 here |
|---|---|---|---|---|
| `bottom_center` | +0.466 | yes | 0.019 | yes |
| `three_point_any` | +0.349 | yes | 0.015 | yes |
| `three_point_majority` | +0.467 | yes | 0.020 | yes |
| `three_point_all` | +0.479 | yes | 0.072 | no (recall) |
| `bottom_strip` | +0.425 | yes | 0.022 | yes |

What I read from it:

- **Box overlap over-reports a lot.** 14,755 of its 27,035 positives are
  people whose feet are outside the zone (precision 0.45, FPR 0.20). In the
  boundary plot its error rate is 0.53–0.77 for people standing 10–50 px
  outside the edge, where `bottom_center` stays below 0.02.
- **Recall is limited by the detector, not by the rules.** 6,837 of about
  7,400 false negatives are people YOLOv8n did not detect at all. On detected
  people the foot-point rules keep 0.95 recall (`three_point_all` 0.87).
- **`three_point_majority` is practically the same rule as `bottom_center`.**
  The three anchors lie on one horizontal line, and for a convex zone "two of
  three inside" implies the middle point is inside. They differ on 39 of
  91,809 person instances (McNemar p = 0.11).
- **`three_point_all` is the most precise but loses people on the edge**
  (error 0.68 in the first 10 px inside the zone). `three_point_any` still
  keeps about a fifth of box overlap's false alarms (2,929 against 14,755).
- **E2 recommendation:** keep `bottom_center` as the default active rule (best
  F1 and event F1; the boundary curve is the narrowest around 0 px). This has
  to be confirmed with manual GT, because derived GT favours it.

Detector (YOLOv8n, conf 0.30, `imgsz` 640) against MOT17 GT boxes at IoU 0.5:

| Sequence | Frames | GT persons | Recall | Precision |
|---|---|---|---|---|
| MOT17-02 | 600 | 9,393 | 0.413 | 0.843 |
| MOT17-04 | 1,050 | 39,821 | 0.456 | 0.911 |
| MOT17-09 | 525 | 3,745 | 0.896 | 0.888 |
| All | 2,175 | 52,959 | 0.480 | 0.897 |

MOT17-02 and -04 contain many small and partly hidden people far from the
camera; at 640 px input YOLOv8n misses most of them. MOT17-09 is close-up and
the detector finds 90 %. A phone at ~2 m from a kerb is closer to MOT17-09.

Hysteresis sweep (E3), pooled event metrics for `bottom_center`:

| Enter / exit frames | Predicted events | True events | Event P | Event R | Event F1 | Duplicate rate |
|---|---|---|---|---|---|---|
| 1 / 1 (no hysteresis) | 79 | 25 | 0.291 | 0.920 | 0.442 | 0.114 |
| 3 / 5 (default) | 27 | 23 | 0.630 | 0.739 | 0.680 | 0.000 |
| 3 / 10 | 22 | 20 | 0.682 | 0.750 | 0.714 | 0.000 |
| 5 / 10 | 22 | 18 | 0.591 | 0.722 | 0.650 | 0.000 |

All rules and all nine settings are in the full report. The duplicate rate
drops to 0 with 3 in / 5 out for every rule except `three_point_any`
(0.149 → 0.038, −74 %). Event counts are small because the GT stays occupied
for long stretches in crowded scenes, so E3 should be repeated on the owner's
zones.

Offline latency per frame (1920×1080 MOT17 JPEGs, MacBook Air M1, MPS,
2,175 frames):

| Span | p50 (ms) | p99 (ms) |
|---|---|---|
| JPEG decode | 6.9 | 8.2 |
| YOLOv8n inference | 17.8 | 24.0 |
| All six rules × 3 zones | 2.2 | 3.7 |
| Total | 26.7 | 34.4 |

### E5: latest-frame mailbox vs FIFO (`make test-load`, 2026-11-05)

3 simulated phones at 15 fps for 60 s each, `FakeDetector` sleeping 20 ms
(so the worker is slower than the offered 45 frames/s), MacBook Air M1:

| Strategy | Processed fps per camera | Drop ratio | Total p50 / p99 (ms) | Queue wait p50 / p99 (ms) | Fairness | Backlog at end |
|---|---|---|---|---|---|---|
| `latest` | 13.0–13.1 | 0.13 | 59.5 / 93.2 | 34.7 / 66.9 | 1.00 | 0 |
| `fifo` | 13.3 | 0.00 | 3,873 / 7,659 | 3,849 / 7,632 | 1.00 | 299 |

p99 queue wait is **99 % lower** under `latest` (criterion: ≥ 30 %), and the
fairness ratio is 1.00 (criterion: ≤ 1.3), so **E5 is met**. Server memory
stayed flat (152.7 → 132.7 MB). FIFO drops nothing but serves frames that are
seconds old and its queue keeps growing.

### Server-side latency with the real model (towards E6)

`LOADTEST_DETECTOR=yolo LOADTEST_SOURCE=data/loadtest-frames LOADTEST_FPS=10 make test-load`:
3 simulated phones at 10 fps sending 60 MOT17-04 frames resized to 1280×720
(JPEG quality 65), real YOLOv8n on MPS, 60 s:

| Camera | Processed fps | Total p50 / p99 (ms) | Queue wait p50 / p99 (ms) |
|---|---|---|---|
| loadtest-1 | 10.0 | 51.3 / 69.1 | 34.2 / 44.9 |
| loadtest-2 | 10.0 | 38.0 / 50.8 | 18.4 / 25.7 |
| loadtest-3 | 10.0 | 22.1 / 31.6 | 0.0 / 0.1 |

The model keeps up with 30 frames/s, so nothing queues (FIFO gave the same
numbers) and receive→publish stays at p50 ≤ 51 ms and p99 ≤ 69 ms, inside the
design target of Section D (p50 ≤ 150 ms, p99 ≤ 400 ms). The three cameras
differ only because the fake phones send in lock-step and the round-robin
serves them in a fixed order. Network time and the phone side are not
included; that is E6.

## What is still open

These need the owner (or hardware) and are listed again in the pull request:

1. **Draw the real zones** for MOT17-02, -04 and -09 in `data/zones/` (remove
   the `TODO` key) and **annotate `in_zone` by hand** with `eval annotate`
   (ideally two annotators, then `eval agreement`). Then run
   `make eval CONFIG=configs/e1_mot17.yaml` for the headline E1/E2/E3 result.
2. **Second data source for E1:** own recordings (`EVAL_RECORD=1`, with the
   consent of the people filmed) with MOT-format boxes, or WILDTRACK once its
   licence is confirmed.
3. **E6** on real phones: a 5-minute session per phone count with the metrics
   export.
4. **E4** (tracking) is P1 and not implemented.
