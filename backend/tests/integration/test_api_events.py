"""M4: /api/events filters, cursor paging, validation and CSV shape."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from publicsafe.db.base import session_scope
from publicsafe.db.models import Event
from tests.conftest import tc_camera, tc_session


def seed(tc: TestClient, n: int = 7) -> tuple[str, str]:
    s = tc_session(tc)
    cam = tc_camera(tc, s["id"], "Gate")
    base = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.UTC)
    with session_scope(tc.app.state.sessionmaker) as db:
        for i in range(n):
            db.add(
                Event(
                    session_id=s["id"],
                    camera_id=cam["camera_id"],
                    zone_id=None,
                    zone_name="z",
                    type="zone_entered" if i % 2 == 0 else "zone_cleared",
                    t_server=base + dt.timedelta(seconds=i),
                    frame_seq=i,
                    count_before=0,
                    count_after=1,
                    active_rule="bottom_center",
                    rule_counts={"bottom_center": 1},
                )
            )
    return s["id"], cam["camera_id"]


def test_list_filters_and_cursor(tc: TestClient) -> None:
    session_id, camera_id = seed(tc)
    page = tc.get("/api/events", params={"limit": 3}).json()
    assert [e["frame_seq"] for e in page["items"]] == [6, 5, 4] and page["next_cursor"]
    page2 = tc.get("/api/events", params={"limit": 3, "cursor": page["next_cursor"]}).json()
    assert [e["frame_seq"] for e in page2["items"]] == [3, 2, 1]
    page3 = tc.get("/api/events", params={"limit": 3, "cursor": page2["next_cursor"]}).json()
    assert [e["frame_seq"] for e in page3["items"]] == [0] and page3["next_cursor"] is None

    entered = tc.get("/api/events", params={"type": "zone_entered"}).json()["items"]
    assert len(entered) == 4
    assert tc.get("/api/events", params={"type": "bogus"}).status_code == 400
    assert tc.get("/api/events", params={"cursor": "junk"}).status_code == 400
    assert tc.get("/api/events", params={"session_id": "nope"}).json()["items"] == []
    window = tc.get(
        "/api/events",
        params={
            "from": "2026-10-10T12:00:02Z",
            "to": "2026-10-10T12:00:04Z",
            "camera_id": camera_id,
        },
    ).json()["items"]
    assert [e["frame_seq"] for e in window] == [4, 3, 2]
    assert tc.get("/api/events", params={"limit": 501}).status_code == 400
    assert window[0]["session_id"] == session_id and window[0]["created_at"].endswith("Z")


def test_export_csv_columns(tc: TestClient) -> None:
    seed(tc, 2)
    r = tc.get("/api/events/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == (
        "id,session_id,session_name,camera_id,camera_name,zone_id,zone_name,type,t_server,"
        "frame_seq,count_before,count_after,active_rule,bbox_overlap,bottom_center,"
        "three_point_any,three_point_majority,three_point_all,bottom_strip"
    )
    assert len(lines) == 3 and ",Gate," in lines[1] and lines[1].endswith(",1,,,,")
