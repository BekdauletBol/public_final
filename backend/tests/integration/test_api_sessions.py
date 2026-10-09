"""M1: session CRUD (F-SES-1, F.2) and cascading delete."""

from __future__ import annotations

import httpx

from tests.conftest import make_camera, make_session, make_zone


async def test_create_list_get(admin: httpx.AsyncClient) -> None:
    assert (await admin.get("/api/sessions")).json() == []
    created = await make_session(admin, "  Main square ")
    assert created["name"] == "Main square"
    assert created["camera_count"] == 0 and created["archived_at"] is None
    assert created["created_at"].endswith("Z") and "." in created["created_at"]

    rows = (await admin.get("/api/sessions")).json()
    assert [r["id"] for r in rows] == [created["id"]]

    detail = (await admin.get(f"/api/sessions/{created['id']}")).json()
    assert detail["cameras"] == [] and detail["name"] == "Main square"

    r = await admin.get("/api/sessions/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "session_not_found"


async def test_validation(admin: httpx.AsyncClient) -> None:
    for body in ({"name": ""}, {"name": "x" * 81}, {"name": "ok", "location_note": "n" * 201}, {}):
        r = await admin.post("/api/sessions", json=body)
        assert r.status_code == 400, body
        assert r.json()["error"]["code"] == "validation_error"
    r = await admin.post("/api/sessions", json={"name": "ok", "extra": 1})
    assert r.status_code == 400


async def test_patch_rename_and_archive(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    r = await admin.patch(
        f"/api/sessions/{s['id']}", json={"name": "Renamed", "location_note": "NE corner"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed" and r.json()["location_note"] == "NE corner"

    r = await admin.patch(f"/api/sessions/{s['id']}", json={"archived": True})
    assert r.json()["archived_at"] is not None
    assert (await admin.get("/api/sessions")).json() == []
    rows = (await admin.get("/api/sessions", params={"include_archived": "true"})).json()
    assert len(rows) == 1 and rows[0]["archived_at"] is not None

    r = await admin.patch(f"/api/sessions/{s['id']}", json={"archived": False})
    assert r.json()["archived_at"] is None


async def test_camera_counts(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    await make_camera(admin, s["id"], "a")
    cam_b = await make_camera(admin, s["id"], "b")
    await admin.post(f"/api/cameras/{cam_b['camera_id']}/revoke", json={})
    row = (await admin.get("/api/sessions")).json()[0]
    assert row["camera_count"] == 1 and row["online_count"] == 0  # revoked not counted
    detail = (await admin.get(f"/api/sessions/{s['id']}")).json()
    assert {c["name"]: c["status"] for c in detail["cameras"]} == {"a": "paired", "b": "revoked"}


async def test_delete_cascades(admin: httpx.AsyncClient) -> None:
    s = await make_session(admin)
    cam = await make_camera(admin, s["id"])
    zone = await make_zone(admin, cam["camera_id"])

    r = await admin.delete(f"/api/sessions/{s['id']}")
    assert r.status_code == 204
    assert (await admin.get(f"/api/sessions/{s['id']}")).status_code == 404
    assert (await admin.get(f"/api/cameras/{cam['camera_id']}")).status_code == 404
    assert (await admin.get(f"/api/zones/{zone['id']}")).status_code in (404, 405)
    assert (await admin.get("/api/cameras")).json() == []
    assert (await admin.delete(f"/api/sessions/{s['id']}")).status_code == 404
