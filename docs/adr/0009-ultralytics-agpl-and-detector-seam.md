# ADR 0009: AGPL-3.0 ultralytics behind the `Detector` seam

Status: Accepted (M4, 2026-10-10)

## Context

YOLOv8n ships in the `ultralytics` package, licensed AGPL-3.0. The licence
obliges anyone who distributes the software, or offers it as a network
service, to make the complete corresponding source available under AGPL
(an alternative commercial licence exists). This research prototype runs on
one laptop for its owner, which does not trigger the network-service clause,
but the obligation matters the moment it is deployed for others.

## Decision

- `publicsafe/core/detector.py` defines a minimal `Detector` protocol
  (`detect(image, conf) -> list[Detection]`). `UltralyticsDetector` is the
  only module that imports ultralytics, and it imports lazily so the rest of
  `core` works without it.
- Tests and the load test use `FakeDetector`; the pass-through `NullDetector`
  exists for diagnostics. `DETECTOR=yolo|fake|none` selects at start.
- The model file's SHA-256 is recorded and shown on the Health page so results
  can be tied to exact weights.
- If the project is ever offered as a service, either publish the full source
  under AGPL-3.0 (the repository already contains everything needed) or swap
  the adapter for a detector with a permissive licence (an ONNX runtime or a
  torchvision model); nothing outside `detector.py` changes.

## Consequences

- No code outside the adapter depends on ultralytics' API, which also makes
  the P1 tracking option (one model per camera) a local change.
- The repository's own licence choice is left to the owner; this ADR records
  the constraint, not a decision to adopt AGPL.
