"""M1: polygon validation and normalisation (F-ZONE-2)."""

from __future__ import annotations

import pytest

from publicsafe.core.geometry import (
    PolygonError,
    aspect_changed,
    denormalise,
    normalise,
    validate_polygon,
)

SQUARE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]


def test_valid_square_returns_tuples() -> None:
    assert validate_polygon(SQUARE) == [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]


def test_boundary_coordinates_are_allowed() -> None:
    validate_polygon([[0, 0], [1, 0], [1, 1]])


@pytest.mark.parametrize(
    ("points", "reason"),
    [
        ([[0, 0], [1, 0]], "too_few_points"),
        ([[i / 25, (i % 2) / 2] for i in range(21)], "too_many_points"),
        ([[0, 0], [1.2, 0], [1, 1]], "out_of_range"),
        ([[0, 0], [-0.1, 0.5], [1, 1]], "out_of_range"),
        ([[0, 0], [1, 1], [1, 0], [0, 1]], "not_simple"),  # bow-tie
        ([[0, 0], [1, 0], [0, 0]], "not_simple"),  # repeated vertex, zero area
        ([[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]], "not_simple"),
        ([[0, 0], [1, 0], [1, 1e-5]], "degenerate"),  # valid but ~zero area
        ([[0, 0], [1], [1, 1]], "bad_point"),
        ([[0, 0], ["a", 0], [1, 1]], "bad_point"),
        ([[0, 0], [float("nan"), 0], [1, 1]], "out_of_range"),
    ],
)
def test_invalid_polygons(points: list, reason: str) -> None:
    with pytest.raises(PolygonError) as excinfo:
        validate_polygon(points)
    assert excinfo.value.reason == reason


def test_normalise_roundtrip() -> None:
    px = [(128, 72), (1152, 72), (640, 648)]
    norm = normalise(px, 1280, 720)
    assert norm[0] == (0.1, 0.1)
    back = denormalise(norm, 1280, 720)
    assert all(
        abs(a - b) < 1e-9
        for (a, b), (c, d) in zip(back, px, strict=True)
        for a, b in [(a, c), (b, d)]
    )


def test_normalise_rejects_bad_frame() -> None:
    with pytest.raises(ValueError):
        normalise([(0, 0)], 0, 720)


def test_aspect_changed() -> None:
    assert not aspect_changed(1280, 720, 1920, 1080)  # same 16:9
    assert not aspect_changed(1280, 720, 1280, 724)  # 0.6 % change
    assert aspect_changed(1280, 720, 720, 1280)  # rotated
    assert aspect_changed(1280, 720, 1280, 960)  # 4:3
    assert aspect_changed(0, 720, 1280, 720)
