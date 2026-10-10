"""``make test-model``: the real YOLOv8n on a golden image (needs weights; downloads once)."""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import pytest

from publicsafe.core.detector import UltralyticsDetector
from publicsafe.core.models import Detection
from publicsafe.core.rules import RuleId, ZonePolygon, compute_all_rules

pytestmark = pytest.mark.model

FIXTURES = Path(__file__).parent / "fixtures"
#: Golden image: ultralytics' sample ``bus.jpg`` (four pedestrians in front of a bus),
#: downloaded on first run so no photo of people is committed to the repository.
GOLDEN = FIXTURES / "person_in_zone.jpg"
#: Lower band of the image where the pedestrians' feet are (normalised).
ZONE = [[0.0, 0.78], [1.0, 0.78], [1.0, 1.0], [0.0, 1.0]]


def ensure_golden() -> Path:
    if GOLDEN.exists():
        return GOLDEN
    from ultralytics.utils.downloads import safe_download

    FIXTURES.mkdir(parents=True, exist_ok=True)
    safe_download("https://ultralytics.com/images/bus.jpg", file=GOLDEN)
    assert GOLDEN.exists(), "could not download the golden image"
    return GOLDEN


@pytest.fixture(scope="module")
def detector() -> UltralyticsDetector:
    model_path = Path(os.environ.get("MODEL_PATH", "../data/models/yolov8n.pt"))
    det = UltralyticsDetector(model_path, device=os.environ.get("INFERENCE_DEVICE") or None)
    det.warm_up()
    return det


def test_golden_image_person_detected_inside_zone(detector: UltralyticsDetector) -> None:
    image = cv2.imread(str(ensure_golden()))
    assert image is not None
    h, w = image.shape[:2]
    dets: list[Detection] = detector.detect(image, conf=0.3)
    assert len(dets) >= 1
    assert all(d.conf >= 0.3 and d.cls == 0 for d in dets)
    zone = ZonePolygon(ZONE, w, h)
    hits, counts = compute_all_rules(dets, zone)
    assert counts[RuleId.BOTTOM_CENTER] >= 1, f"no bottom-centre inside the zone: {dets}"
    assert (
        hits[RuleId.THREE_POINT_ANY] and counts[RuleId.BBOX_OVERLAP] >= counts[RuleId.BOTTOM_CENTER]
    )
    assert len(detector.model_sha256) == 64 and detector.device in ("mps", "cuda", "cpu")
    print(
        f"\ndevice={detector.device} warmup={detector.warmup_ms:.0f} ms "
        f"detections={len(dets)} in_zone={counts[RuleId.BOTTOM_CENTER]} "
        f"sha256={detector.model_sha256[:12]}"
    )


def test_fp16_measured_against_fp32(detector: UltralyticsDetector) -> None:
    """Report whether INFERENCE_HALF would be faster and still correct (H.2)."""
    import time

    if detector.device == "cpu":
        pytest.skip("FP16 only applies to mps/cuda")
    image = cv2.imread(str(ensure_golden()))
    half = UltralyticsDetector(detector.model_path, device=detector.device, half=True)
    half.warm_up()

    def bench(det: UltralyticsDetector, n: int = 20) -> float:
        t0 = time.perf_counter()
        for _ in range(n):
            det.detect(image, conf=0.3)
        return (time.perf_counter() - t0) * 1000 / n

    fp32_ms, fp16_ms = bench(detector), bench(half)
    fp32 = sorted(round(d.conf, 2) for d in detector.detect(image, conf=0.3))
    fp16 = sorted(round(d.conf, 2) for d in half.detect(image, conf=0.3))
    print(f"\nfp32 {fp32_ms:.1f} ms/frame conf={fp32}\nfp16 {fp16_ms:.1f} ms/frame conf={fp16}")
    assert len(fp16) >= 1  # FP16 must still detect people; speed is reported, not asserted
