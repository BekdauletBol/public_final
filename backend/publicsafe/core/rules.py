"""Membership rule identifiers (Section H.3). ``compute_all_rules`` arrives in M4."""

from __future__ import annotations

from enum import StrEnum


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
