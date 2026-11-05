# ADR 0010: Evaluation data: partial MOT17 fetch, guarded derived GT, metric conventions

Status: Accepted (M5, 2026-11-05)

## Context

Spec I asks for `eval fetch mot17` with SHA-256 verification, in-zone ground
truth drawn by people, and the metrics of I.4. Three practical problems came
up while building it:

- The official `MOT17.zip` is 5.9 GB. On the development network it came at
  about 40-300 KB/s, so a full download takes many hours. The archive has no
  published checksum, and we need only three train sequences (and only one of
  the three identical image copies per sequence).
- Manual in-zone labels take the owner hours per sequence and zone. The spec
  allows `source=derived` labels for sanity checks only, and that rule must be
  hard to break by accident.
- Spec I.4 leaves a few counting details open: what an unmatched detection
  with a negative rule is, whether detections on ignore regions count in the
  frame and event levels, and which persons the boundary analysis uses.

## Decision

- **Partial fetch with range requests.** `eval fetch` reads the archive's
  central directory over HTTP `Range` and downloads only
  `train/<sequence>-FRCNN/` (images, `gt/`, `seqinfo.ini`; `det/` is skipped).
  The remote archive must still have the recorded size
  (5,860,214,001 bytes); each file is checked against its CRC-32 from the
  archive; each extracted sequence gets a `MANIFEST.sha256`, whose SHA-256 was
  recorded after the first download and is verified on later fetches.
  `--archive` accepts a full local copy instead.
- **Derived GT is opt-in.** A run whose GT for some zone is only
  `source=derived` stops with an error unless the config sets
  `allow_derived_gt: true`; the report then opens with a "sanity check only"
  banner. The headline config `configs/e1_mot17.yaml` keeps it `false`.
- **Counting conventions** (written into `eval/metrics.py`):
  unmatched detection + rule negative = TN (so FPR has a denominator);
  detections dropped on ignore regions are excluded everywhere (person, frame
  and event level); the boundary analysis uses matched persons only, because
  a detector miss is the same error for every rule and would hide the rule
  differences (misses are reported as `fn_missed_detection`).
- A GT row with `person_id=0` marks "annotated, nobody here", so an empty
  annotated frame is not mistaken for a skipped one.

## Alternatives

- Download the full archive once: correct but impractical here, and the
  only checksum would still be one we computed ourselves.
- A third-party mirror (e.g. on a model hub): faster, but not the official
  source, and the spec forbids silent substitution.
- Let derived GT run with only a warning: too easy to report by mistake.

## Consequences

- A fresh fetch of the three default sequences is about 465 MB and took
  about 2 minutes on a good connection; resuming skips complete files.
- If MOTChallenge ever changes the archive, the size check fails with a
  message to download it in full and record new values.
- Numbers from derived GT are clearly labelled in `docs/EVALUATION.md` and in
  every report; the E1 headline still needs manual labels.
