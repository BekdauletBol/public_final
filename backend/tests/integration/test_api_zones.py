"""M1: zone endpoints with polygon validation (F-ZONE-2/3, F.5)."""

from __future__ import annotations

import httpx

from tests.conftest import SQUARE, make_camera, make_session, make_zone


async def test_create_and_list(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"])
    cid = cam["camera_id"]
    zone = await make_zone(admin, cid)
    assert zone["points"] == SQUARE
    assert zone["active_rule"] == "bottom_center"
    assert zone["enter_frames"] == 3 and zone["exit_frames"] == 5
    assert zone["enabled"] is True and zone["needs_review"] is False
    assert zone["frame_w"] == 1280 and zone["frame_h"] == 720

    rows = (await admin.get(f"/api/cameras/{cid}/zones")).json()
    assert [z["id"] for z in rows] == [zone["id"]]
    detail = (await admin.get(f"/api/cameras/{cid}")).json()
    assert detail["zones"][0]["id"] == zone["id"]
    assert (await admin.get("/api/cameras/nope/zones")).status_code == 404


async def test_polygon_validation_errors(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"])
    cid = cam["camera_id"]
    cases = {
        "too_few_points": [[0, 0], [1, 0]],
        "out_of_range": [[0, 0], [1.5, 0], [1, 1]],
        "not_simple": [[0, 0], [1, 1], [1, 0], [0, 1]],
        "degenerate": [[0, 0], [1, 0], [1, 1e-6]],
    }
    for reason, points in cases.items():
        r = await admin.post(
            f"/api/cameras/{cid}/zones",
            json={"name": "z", "points": points, "frame_w": 1280, "frame_h": 720},
        )
        assert r.status_code == 400, reason
        assert r.json()["error"]["code"] == "polygon_invalid"
        assert r.json()["error"]["details"]["reason"] == reason
        assert r.json()["error"]["message"]

    for bad in (
        {"name": "z", "points": SQUARE, "frame_w": 0, "frame_h": 720},
        {"name": "z", "points": SQUARE, "frame_w": 1280, "frame_h": 720, "enter_frames": 0},
        {"name": "z", "points": SQUARE, "frame_w": 1280, "frame_h": 720, "exit_frames": 61},
        {"name": "z", "points": SQUARE, "frame_w": 1280, "frame_h": 720, "active_rule": "magic"},
        {"name": "z", "points": SQUARE},
    ):
        r = await admin.post(f"/api/cameras/{cid}/zones", json=bad)
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error", bad


async def test_patch_and_soft_delete(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"])
    zone = await make_zone(admin, cam["camera_id"])
    zid = zone["id"]

    r = await admin.patch(
        f"/api/zones/{zid}",
        json={
            "name": "kerb",
            "active_rule": "three_point_majority",
            "enabled": False,
            "exit_frames": 8,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "kerb" and body["active_rule"] == "three_point_majority"
    assert body["enabled"] is False and body["exit_frames"] == 8
    assert body["updated_at"] >= zone["updated_at"]

    r = await admin.patch(f"/api/zones/{zid}", json={"points": [[0, 0], [1, 1], [1, 0], [0, 1]]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "polygon_invalid"

    assert (await admin.delete(f"/api/zones/{zid}")).status_code == 204
    assert (await admin.get(f"/api/cameras/{cam['camera_id']}/zones")).json() == []
    assert (await admin.patch(f"/api/zones/{zid}", json={"name": "x"})).status_code == 404
    assert (await admin.delete(f"/api/zones/{zid}")).status_code == 404
    row = (await admin.get("/api/cameras")).json()[0]
    assert row["zone_count"] == 0
