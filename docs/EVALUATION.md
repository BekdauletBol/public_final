# Evaluation

The experiment harness (`publicsafe eval ...`), datasets, metrics and the
pre-registered success criteria are added in M5. This file already records the
facts established in M4.

## Model

| Item | Value |
|---|---|
| Weights | `yolov8n.pt` (ultralytics release asset, downloaded on first start into `data/models/`) |
| SHA-256 | `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36` |
| Device on the development Mac | `mps` (Apple Silicon, macOS 26.2) |
| Warm-up (first inference) | 3.2 s |
| Classes | 0 (person) only; confidence from the camera settings (default 0.30); `imgsz` 640 |

## FP16 on MPS (H.2 measurement, 2026-10-10, `make test-model`)

Golden image `bus.jpg` (810×1080), 20 inferences each after warm-up:

| Precision | ms / frame | Detections (conf) |
|---|---|---|
| FP32 (`INFERENCE_HALF=false`) | 49.2 | 0.83, 0.85, 0.87 |
| FP16 (`INFERENCE_HALF=true`) | 57.6 | 0.82, 0.85, 0.87 |

FP16 was **slower** on this Mac's MPS backend with identical boxes, so
`INFERENCE_HALF` stays `false`. Re-measure with `make test-model` on other
hardware before enabling it.
