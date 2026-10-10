"""Membership rules (H.3): every rule is computed for every detection and zone, every frame."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

import shapely
from shapely.geometry import Point, Polygon, box

from publicsafe.core.models import Detection


class RuleId(StrEnum):
    """Every membership rule, in the order used by CSV columns and reports."""

    BBOX_OVERLAP = "bbox_overlap"
    BOTTOM_CENTER = "bottom_center"
    THREE_POINT_ANY = "three_point_any"
    THREE_POINT_MAJORITY = "three_point_majority"
    THREE_POINT_ALL = "three_point_all"
    BOTTOM_STRIP = "bottom_strip"


#: Default ``active_rule`` for new zones (F-ZONE-3).
DEFAULT_RULE = RuleId.BOTTOM_CENTER

#: Short descriptions shown in the zone editor's rule selector.
RULE_DESCRIPTIONS: dict[RuleId, str] = {
    RuleId.BBOX_OVERLAP: "Any overlap between the person box and the zone (baseline)",
    RuleId.BOTTOM_CENTER: "Bottom-centre of the box (feet) inside the zone",
    RuleId.THREE_POINT_ANY: "Any of bottom-left, bottom-centre, bottom-right inside",
    RuleId.THREE_POINT_MAJORITY: "At least two of the three bottom points inside",
    RuleId.THREE_POINT_ALL: "All three bottom points inside",
    RuleId.BOTTOM_STRIP: "Lower 10 % strip of the box at least half inside the zone",
}

ALL_RULES: tuple[RuleId, ...] = tuple(RuleId)
STRIP_FRACTION = 0.1
STRIP_MIN_COVERAGE = 0.5


class ZonePolygon:
    """A zone polygon in pixels for one frame size, prepared once (H.3)."""

    def __init__(self, points_norm: Sequence[Sequence[float]], w: int, h: int) -> None:
        self.w, self.h = w, h
        self.polygon = Polygon([(float(x) * w, float(y) * h) for x, y in points_norm])
        shapely.prepare(self.polygon)

    def covers_point(self, x: float, y: float) -> bool:
        """Boundary counts as inside (``covers``)."""
        return bool(self.polygon.covers(Point(x, y)))

    def rules_for(self, det: Detection) -> dict[RuleId, bool]:
        """Evaluate all six rules for one detection."""
        x1, y1, x2, y2 = det.bbox_xyxy
        anchors = det.anchors
        bl = self.covers_point(*anchors["bl"])
        bc = self.covers_point(*anchors["bc"])
        br = self.covers_point(*anchors["br"])
        inside = bl + bc + br

        body = box(x1, y1, x2, y2)
        overlap = body.area > 0 and self.polygon.intersects(body)
        if overlap:
            overlap = self.polygon.intersection(body).area > 0.0

        strip = box(x1, y2 - STRIP_FRACTION * (y2 - y1), x2, y2)
        strip_ok = False
        if strip.area > 0:
            strip_ok = self.polygon.intersection(strip).area >= STRIP_MIN_COVERAGE * strip.area

        return {
            RuleId.BBOX_OVERLAP: bool(overlap),
            RuleId.BOTTOM_CENTER: bc,
            RuleId.THREE_POINT_ANY: inside >= 1,
            RuleId.THREE_POINT_MAJORITY: inside >= 2,
            RuleId.THREE_POINT_ALL: inside == 3,
            RuleId.BOTTOM_STRIP: bool(strip_ok),
        }


def compute_all_rules(
    detections: Sequence[Detection], zone: ZonePolygon
) -> tuple[dict[RuleId, list[int]], dict[RuleId, int]]:
    """``rule_hits`` (detection indices per rule) and ``counts`` for one zone (F-CV-2)."""
    hits: dict[RuleId, list[int]] = {rule: [] for rule in ALL_RULES}
    for index, det in enumerate(detections):
        for rule, positive in zone.rules_for(det).items():
            if positive:
                hits[rule].append(index)
    counts = {rule: len(indices) for rule, indices in hits.items()}
    return hits, counts


def check_invariants(hits: dict[RuleId, list[int]]) -> list[str]:
    """Violations of ``all ⊆ majority ⊆ any ⊆ bbox_overlap`` and ``bottom_center ⊆ any`` (E2)."""
    sets = {rule: set(indices) for rule, indices in hits.items()}
    chain = [
        (RuleId.THREE_POINT_ALL, RuleId.THREE_POINT_MAJORITY),
        (RuleId.THREE_POINT_MAJORITY, RuleId.THREE_POINT_ANY),
        (RuleId.THREE_POINT_ANY, RuleId.BBOX_OVERLAP),
        (RuleId.BOTTOM_CENTER, RuleId.THREE_POINT_ANY),
    ]
    return [
        f"{small} ⊄ {large}: {sorted(sets[small] - sets[large])}"
        for small, large in chain
        if not sets[small] <= sets[large]
    ]
