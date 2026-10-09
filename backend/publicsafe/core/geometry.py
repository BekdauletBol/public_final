"""Polygon validation and coordinate normalisation (F-ZONE-2).

Anchors and membership rules are added in M4. This module imports only shapely
and the standard library.
"""

from __future__ import annotations

from collections.abc import Sequence

from shapely.geometry import Polygon

Point = tuple[float, float]

MIN_POINTS = 3
MAX_POINTS = 20
MIN_AREA = 1e-4


class PolygonError(ValueError):
    """A zone polygon failed validation; ``reason`` is machine-readable."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def validate_polygon(points: Sequence[Sequence[float]]) -> list[Point]:
    """Check a normalised polygon and return it as a list of ``(x, y)`` tuples.

    Rules: 3..20 vertices, every coordinate within ``[0, 1]``, simple polygon
    (``shapely.Polygon.is_valid``) with area above ``MIN_AREA``.
    Raises :class:`PolygonError` with reasons ``too_few_points``,
    ``too_many_points``, ``bad_point``, ``out_of_range``, ``not_simple`` or
    ``degenerate``.
    """
    if len(points) < MIN_POINTS:
        raise PolygonError("too_few_points", f"A zone needs at least {MIN_POINTS} points")
    if len(points) > MAX_POINTS:
        raise PolygonError("too_many_points", f"A zone may have at most {MAX_POINTS} points")

    coords: list[Point] = []
    for index, raw in enumerate(points):
        if len(raw) != 2:
            raise PolygonError("bad_point", f"Point {index} must be [x, y]")
        try:
            x, y = float(raw[0]), float(raw[1])
        except (TypeError, ValueError) as exc:
            raise PolygonError("bad_point", f"Point {index} is not numeric") from exc
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0) or x != x or y != y:
            raise PolygonError("out_of_range", f"Point {index} is outside [0, 1]")
        coords.append((x, y))

    polygon = Polygon(coords)
    if not polygon.is_valid:
        raise PolygonError("not_simple", "Polygon edges must not cross or repeat")
    if polygon.area <= MIN_AREA:
        raise PolygonError("degenerate", "Polygon area is too small")
    return coords


def normalise(points_px: Sequence[Sequence[float]], w: int, h: int) -> list[Point]:
    """Pixel coordinates on a ``w x h`` frame to ``[0, 1]^2``."""
    if w <= 0 or h <= 0:
        raise ValueError("frame size must be positive")
    return [(float(x) / w, float(y) / h) for x, y in points_px]


def denormalise(points_norm: Sequence[Sequence[float]], w: int, h: int) -> list[Point]:
    """``[0, 1]^2`` coordinates to pixels on a ``w x h`` frame."""
    return [(float(x) * w, float(y) * h) for x, y in points_norm]


def aspect_changed(old_w: int, old_h: int, new_w: int, new_h: int, tolerance: float = 0.01) -> bool:
    """True when the aspect ratio differs by more than ``tolerance`` (F-ZONE-4)."""
    if min(old_w, old_h, new_w, new_h) <= 0:
        return True
    old = old_w / old_h
    new = new_w / new_h
    return abs(new - old) / old > tolerance
