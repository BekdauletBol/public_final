"""M4: membership rules on hand-built cases, invariants by property test, cv2 cross-check (H.3)."""

from __future__ import annotations

import math
import random

import cv2
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from publicsafe.core.models import Detection
from publicsafe.core.rules import (
    ALL_RULES,
    RuleId,
    ZonePolygon,
    check_invariants,
    compute_all_rules,
)

W, H = 1280, 720
#: Rectangle covering the lower middle of the frame: x 0.25..0.75, y 0.5..0.9
ZONE = ZonePolygon([[0.25, 0.5], [0.75, 0.5], [0.75, 0.9], [0.25, 0.9]], W, H)


def det(x1: float, y1: float, x2: float, y2: float) -> Detection:
    return Detection((x1, y1, x2, y2), 0.9)


def rules(d: Detection) -> dict[RuleId, bool]:
    return ZONE.rules_for(d)


def test_box_fully_inside() -> None:
    assert all(rules(det(400, 400, 500, 600)).values())


def test_box_fully_outside() -> None:
    assert not any(rules(det(10, 10, 100, 100)).values())
    assert not any(rules(det(400, 100, 500, 300)).values())  # above the zone


def test_upper_body_in_feet_out_only_bbox_overlap() -> None:
    """Straddling the top edge: head/torso inside, feet below the zone (y2 > 0.9 H)."""
    r = rules(det(500, 300, 600, 700))  # feet at y=700 > 648 = bottom edge
    assert r[RuleId.BBOX_OVERLAP]
    assert not r[RuleId.BOTTOM_CENTER] and not r[RuleId.THREE_POINT_ANY]
    assert not r[RuleId.THREE_POINT_MAJORITY] and not r[RuleId.THREE_POINT_ALL]
    assert not r[RuleId.BOTTOM_STRIP]


def test_feet_exactly_on_the_edge_count_as_inside() -> None:
    bottom = 0.9 * H  # 648.0
    r = rules(det(500, 500, 600, bottom))
    assert r[RuleId.BOTTOM_CENTER] and r[RuleId.THREE_POINT_ALL]
    left = 0.25 * W  # 320
    r = rules(det(left - 50, 500, left + 50, 600))  # bc exactly on the left edge
    assert r[RuleId.BOTTOM_CENTER] and r[RuleId.THREE_POINT_ANY]
    assert not r[RuleId.THREE_POINT_ALL]


def test_three_point_variants() -> None:
    left = 0.25 * W
    r = rules(det(left - 90, 500, left + 10, 600))  # only br inside
    assert r[RuleId.THREE_POINT_ANY] and not r[RuleId.THREE_POINT_MAJORITY]
    assert not r[RuleId.BOTTOM_CENTER]
    r = rules(det(left - 30, 500, left + 90, 600))  # bc and br inside, bl outside
    assert r[RuleId.THREE_POINT_MAJORITY] and r[RuleId.BOTTOM_CENTER]
    assert not r[RuleId.THREE_POINT_ALL]


def test_bottom_strip_rule() -> None:
    bottom = 0.9 * H
    r = rules(det(500, 400, 600, bottom + 3))  # strip (30 px high) mostly inside
    assert r[RuleId.BOTTOM_STRIP]
    r = rules(det(500, 400, 600, bottom + 25))  # strip mostly outside
    assert not r[RuleId.BOTTOM_STRIP]


def test_compute_all_rules_and_counts() -> None:
    dets = [det(400, 400, 500, 600), det(10, 10, 100, 100), det(500, 300, 600, 700)]
    hits, counts = compute_all_rules(dets, ZONE)
    assert hits[RuleId.BBOX_OVERLAP] == [0, 2] and counts[RuleId.BBOX_OVERLAP] == 2
    assert hits[RuleId.BOTTOM_CENTER] == [0] and counts[RuleId.THREE_POINT_ALL] == 1
    assert set(hits) == set(ALL_RULES) and check_invariants(hits) == []
    assert compute_all_rules([], ZONE)[1] == {r: 0 for r in ALL_RULES}


def test_check_invariants_detects_violation() -> None:
    hits = {r: [] for r in ALL_RULES}
    hits[RuleId.THREE_POINT_ALL] = [0]
    assert check_invariants(hits) == ["three_point_all ⊄ three_point_majority: [0]"]


# --- property test: invariants on random boxes and random simple polygons ---------------


def star_polygon(rng: random.Random, n: int) -> list[list[float]]:
    """A simple polygon: vertices at sorted angles around a centre (no self-intersections)."""
    cx, cy = rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)
    angles = sorted(rng.uniform(0, 2 * math.pi) for _ in range(n))
    pts = []
    for a in angles:
        r = rng.uniform(0.1, 0.3)
        pts.append(
            [min(1.0, max(0.0, cx + r * math.cos(a))), min(1.0, max(0.0, cy + r * math.sin(a)))]
        )
    return pts


@settings(max_examples=1000, deadline=None)
@given(seed=st.integers(0, 2**31), nboxes=st.integers(0, 6))
def test_invariants_hold_for_random_geometry(seed: int, nboxes: int) -> None:
    rng = random.Random(seed)
    zone = ZonePolygon(star_polygon(rng, rng.randint(3, 9)), W, H)
    if not zone.polygon.is_valid or zone.polygon.area <= 1e-6:
        return
    dets = []
    for _ in range(nboxes):
        x1, y1 = rng.uniform(-50, W), rng.uniform(-50, H)
        dets.append(det(x1, y1, x1 + rng.uniform(1, 400), y1 + rng.uniform(1, 500)))
    hits, counts = compute_all_rules(dets, zone)
    assert check_invariants(hits) == []
    assert all(counts[r] == len(hits[r]) for r in ALL_RULES)


def test_covers_agrees_with_cv2_point_polygon_test() -> None:
    """1,000 random points: shapely covers == cv2.pointPolygonTest >= 0 away from edges."""
    rng = random.Random(42)
    pts_norm = star_polygon(rng, 7)
    zone = ZonePolygon(pts_norm, W, H)
    contour = np.array([[x * W, y * H] for x, y in pts_norm], dtype=np.float32).reshape(-1, 1, 2)
    disagreements = 0
    for _ in range(1000):
        x, y = rng.uniform(0, W), rng.uniform(0, H)
        ours = zone.covers_point(x, y)
        dist = cv2.pointPolygonTest(contour, (x, y), True)
        if abs(dist) <= 1e-6:
            continue  # within tolerance of an edge: either answer accepted
        if ours != (dist >= 0):
            disagreements += 1
    assert disagreements == 0


@pytest.mark.parametrize("w,h", [(640, 480), (1920, 1080)])
def test_polygon_scales_with_frame_size(w: int, h: int) -> None:
    zone = ZonePolygon([[0, 0], [1, 0], [1, 1], [0, 1]], w, h)
    assert zone.polygon.area == pytest.approx(w * h)
    assert zone.covers_point(w, h) and not zone.covers_point(w + 1, h)
