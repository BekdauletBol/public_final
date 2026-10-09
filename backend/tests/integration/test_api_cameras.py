"""M1: camera endpoints (F-CAM-2, F.4) using TEST_MODE cameras."""

from __future__ import annotations

import httpx

from tests.conftest import make_camera, make_session, make_zone


async def test_test_camera_and_detail(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"], "north")
    assert len(cam["device_token"]) >= 43

    rows = (await admin.get("/api/cameras", params={"session_id": s["id"]})).json()
    assert [r["name"] for r in rows] == ["north"]
    assert rows[0]["status"] == "paired" and rows[0]["zone_count"] == 0
    assert rows[0]["fps_in"] is None and rows[0]["occupancy"] == {}

    detail = (await admin.get(f"/api/cameras/{cam['camera_id']}")).json()
    assert detail["settings"] == {
        "target_fps": 10,
        "jpeg_quality": 65,
        "max_long_edge": 1280,
        "confidence": 0.3,
        "privacy_mode": False,
    }
    assert detail["zones"] == [] and detail["health"] == {}

    r = await admin.post("/api/test/cameras", json={"session_id": "missing"})
    assert r.status_code == 404


async def test_patch_name_and_settings(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"])
    cid = cam["camera_id"]
    r = await admin.patch(
        f"/api/cameras/{cid}", json={"name": "gate", "settings": {"target_fps": 5}}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "gate"
    assert r.json()["settings"]["target_fps"] == 5 and r.json()["settings"]["jpeg_quality"] == 65

    for bad in (
        {"settings": {"target_fps": 0}},
        {"settings": {"target_fps": 31}},
        {"settings": {"jpeg_quality": 20}},
        {"settings": {"max_long_edge": 4000}},
        {"settings": {"confidence": 1.5}},
        {"settings": {"unknown": 1}},
        {"name": ""},
    ):
        r = await admin.patch(f"/api/cameras/{cid}", json=bad)
        assert r.status_code == 400, bad

    r = await admin.patch(f"/api/cameras/{cid}", json={"settings": {"privacy_mode": True}})
    assert r.json()["settings"]["privacy_mode"] is True
    r = await admin.get(f"/api/cameras/{cid}/last-frame.jpg")
    assert r.status_code == 404 and r.json()["error"]["code"] == "privacy_mode"


async def test_last_frame_404_before_any_frame(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"])
    r = await admin.get(f"/api/cameras/{cam['camera_id']}/last-frame.jpg")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_frame"


async def test_revoke_and_delete_rules(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"])
    cid = cam["camera_id"]

    r = await admin.post(f"/api/cameras/{cid}/revoke", json={"reason": "lost phone"})
    assert r.status_code == 204
    detail = (await admin.get(f"/api/cameras/{cid}")).json()
    assert detail["status"] == "revoked"

    log = (await admin.get("/api/security/log")).json()
    revoked = [e for e in log if e["kind"] == "token_revoked"]
    assert (
        revoked
        and revoked[0]["camera_id"] == cid
        and revoked[0]["details"] == {"reason": "lost phone"}
    )

    assert (await admin.delete(f"/api/cameras/{cid}")).status_code == 204
    assert (await admin.get(f"/api/cameras/{cid}")).status_code == 404


async def test_delete_never_connected_camera_allowed(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"])
    assert (await admin.delete(f"/api/cameras/{cam['camera_id']}")).status_code == 204


async def test_zone_count_and_needs_review_in_summary(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"])
    await make_zone(admin, cam["camera_id"])
    row = (await admin.get("/api/cameras")).json()[0]
    assert row["zone_count"] == 1 and row["needs_review"] is False
