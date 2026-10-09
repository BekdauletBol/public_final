"""M2: pairing codes and redemption (Section G, F.3)."""

from __future__ import annotations

import datetime as dt
import re
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from publicsafe.db.base import session_scope
from publicsafe.db.models import DeviceCredential, PairingCode
from publicsafe.main import create_app
from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, CSRF, tc_camera, tc_mint, tc_session

LETTERS = r"[BCDFGHJKLMNPQRSTVWXZ]{3}-[BCDFGHJKLMNPQRSTVWXZ]{3}"


def redeem(tc: TestClient, **body: object):
    return tc.post("/api/pair/redeem", json=body)


def test_mint_list_cancel(tc: TestClient) -> None:
    s = tc_session(tc)
    minted = tc_mint(tc, s["id"], camera_name_hint="  north gate ")
    assert re.fullmatch(LETTERS, minted["pin"])
    assert minted["pair_url"] == f"https://testserver/pair?c={minted['code_id']}"
    assert minted["ttl_seconds"] == 300
    expires = dt.datetime.fromisoformat(minted["expires_at"].replace("Z", "+00:00"))
    assert 295 < (expires - dt.datetime.now(dt.UTC)).total_seconds() <= 300

    listed = tc.get(f"/api/sessions/{s['id']}/pairing-codes").json()
    assert len(listed) == 1
    row = listed[0]
    assert row["status"] == "pending" and row["attempts"] == 0 and row["max_attempts"] == 5
    assert row["camera_name_hint"] == "north gate" and row["redeemed_camera_id"] is None
    assert minted["pin"] not in str(listed) and minted["pin"].replace("-", "") not in str(listed)

    assert tc.delete(f"/api/pairing-codes/{minted['code_id']}").status_code == 204
    assert tc.get(f"/api/sessions/{s['id']}/pairing-codes").json()[0]["status"] == "cancelled"
    assert tc.delete(f"/api/pairing-codes/{minted['code_id']}").status_code == 204  # idempotent
    assert tc.delete("/api/pairing-codes/nope").status_code == 404
    assert tc.post("/api/sessions/nope/pairing-codes", json={}).status_code == 404

    r = redeem(tc, pin=minted["pin"], code_id=minted["code_id"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "pin_invalid"


def test_mint_refused_for_archived_or_foreign_replace(tc: TestClient) -> None:
    s = tc_session(tc)
    other = tc_session(tc, "Other")
    cam = tc_camera(tc, other["id"])
    r = tc.post(
        f"/api/sessions/{s['id']}/pairing-codes", json={"replace_camera_id": cam["camera_id"]}
    )
    assert r.status_code == 400 and "replace_camera_id" in r.json()["error"]["details"]["fields"]
    tc.patch(f"/api/sessions/{s['id']}", json={"archived": True})
    r = tc.post(f"/api/sessions/{s['id']}/pairing-codes", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "session_archived"


def test_redeem_happy_path_with_and_without_code_id(tc: TestClient) -> None:
    s = tc_session(tc)
    a = tc_mint(tc, s["id"], camera_name_hint="Gate")
    r = redeem(tc, pin=a["pin"].lower(), code_id=a["code_id"], device_label="iPhone", remember=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["camera_name"] == "Gate" and body["session_name"] == s["name"]
    assert body["ws_url"] == "wss://testserver/ws/camera"
    assert len(body["device_token"]) >= 43
    assert body["settings"]["target_fps"] == 10

    b = tc_mint(tc, s["id"])
    r = redeem(tc, pin=b["pin"])  # no code_id: matched against pending codes
    assert r.status_code == 200, r.text
    assert r.json()["camera_name"] == "Camera 2"

    cams = tc.get(f"/api/sessions/{s['id']}").json()["cameras"]
    assert {c["name"]: c["status"] for c in cams} == {"Gate": "paired", "Camera 2": "paired"}
    assert next(c for c in cams if c["name"] == "Gate")["device_label"] == "iPhone"

    codes = tc.get(f"/api/sessions/{s['id']}/pairing-codes").json()
    assert {c["status"] for c in codes} == {"redeemed"}
    assert {c["redeemed_camera_id"] for c in codes} == {c["id"] for c in cams}

    log = tc.get("/api/security/log").json()
    ok = [e for e in log if e["kind"] == "redeem_ok"]
    assert len(ok) == 2
    assert {e["details"]["remember"] for e in ok} == {True, False}
    assert body["device_token"] not in str(log) and a["pin"] not in str(log)

    r = redeem(tc, pin=a["pin"], code_id=a["code_id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "pin_already_redeemed"


def test_wrong_pins_attempts_and_lock(tc: TestClient) -> None:
    s = tc_session(tc)
    m = tc_mint(tc, s["id"])
    wrong = "BBB-BBB" if m["pin"] != "BBB-BBB" else "CCC-CCC"

    r = redeem(tc, pin="AAA-AAA", code_id=m["code_id"])  # vowels: invalid format, no attempt
    assert r.status_code == 404 and r.json()["error"]["code"] == "pin_invalid"
    assert tc.get(f"/api/sessions/{s['id']}/pairing-codes").json()[0]["attempts"] == 0

    r = redeem(tc, pin=wrong)  # without code_id: no counter changes
    assert r.status_code == 404
    assert tc.get(f"/api/sessions/{s['id']}/pairing-codes").json()[0]["attempts"] == 0

    for attempt in range(1, 5):
        r = redeem(tc, pin=wrong, code_id=m["code_id"])
        assert r.status_code == 404 and r.json()["error"]["code"] == "pin_invalid"
        assert tc.get(f"/api/sessions/{s['id']}/pairing-codes").json()[0]["attempts"] == attempt
    r = redeem(tc, pin=wrong, code_id=m["code_id"])  # 5th wrong attempt
    assert r.status_code == 423 and r.json()["error"]["code"] == "pin_locked"
    row = tc.get(f"/api/sessions/{s['id']}/pairing-codes").json()[0]
    assert row["status"] == "locked" and row["attempts"] == 5

    r = redeem(tc, pin=m["pin"], code_id=m["code_id"])  # correct PIN but locked
    assert r.status_code == 423
    r = redeem(tc, pin=m["pin"])  # locked codes are not candidates
    assert r.status_code == 404

    kinds = [e["kind"] for e in tc.get("/api/security/log").json()]
    assert "pin_locked" in kinds and kinds.count("redeem_failed") == 8
    assert m["pin"] not in str(tc.get("/api/security/log").json())


def test_expired_and_cancelled_and_session_gone(tc: TestClient) -> None:
    s = tc_session(tc)
    m = tc_mint(tc, s["id"])
    factory = tc.app.state.sessionmaker
    with session_scope(factory) as db:
        code = db.get(PairingCode, m["code_id"])
        code.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    assert tc.get(f"/api/sessions/{s['id']}/pairing-codes").json()[0]["status"] == "expired"
    r = redeem(tc, pin=m["pin"], code_id=m["code_id"])
    assert r.status_code == 410 and r.json()["error"]["code"] == "pin_expired"
    r = redeem(tc, pin=m["pin"])
    assert r.status_code == 404  # expired codes are not candidates

    c = tc_mint(tc, s["id"])
    tc.delete(f"/api/pairing-codes/{c['code_id']}")
    r = redeem(tc, pin=c["pin"], code_id=c["code_id"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "pin_invalid"

    g = tc_mint(tc, s["id"])
    tc.patch(f"/api/sessions/{s['id']}", json={"archived": True})
    r = redeem(tc, pin=g["pin"], code_id=g["code_id"])
    assert r.status_code == 410 and r.json()["error"]["code"] == "session_gone"
    codes = {
        c["code_id"]: c["status"] for c in tc.get(f"/api/sessions/{s['id']}/pairing-codes").json()
    }
    assert codes[g["code_id"]] == "cancelled"

    d = tc_mint(tc, tc_session(tc, "Doomed")["id"])
    sid = tc.get("/api/sessions").json()[0]["id"]
    tc.delete(f"/api/sessions/{sid}")
    r = redeem(tc, pin=d["pin"], code_id=d["code_id"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "pin_invalid"


def test_replace_phone_keeps_camera_id(tc: TestClient) -> None:
    s = tc_session(tc)
    cam = tc_camera(tc, s["id"], "Gate")
    m = tc_mint(tc, s["id"], replace_camera_id=cam["camera_id"])
    r = redeem(tc, pin=m["pin"], code_id=m["code_id"], device_label="new phone")
    assert r.status_code == 200
    assert r.json()["camera_id"] == cam["camera_id"]
    assert r.json()["device_token"] != cam["device_token"]
    with session_scope(tc.app.state.sessionmaker) as db:
        creds = list(
            db.scalars(
                select(DeviceCredential).where(DeviceCredential.camera_id == cam["camera_id"])
            )
        )
        assert len(creds) == 2
        revoked = [c for c in creds if c.revoked_at is not None]
        assert len(revoked) == 1 and revoked[0].revoked_reason == "replaced"
    detail = tc.get(f"/api/cameras/{cam['camera_id']}").json()
    assert detail["status"] == "paired" and detail["device_label"] == "new phone"
    assert len(tc.get(f"/api/sessions/{s['id']}").json()["cameras"]) == 1


def test_digits_alphabet(make_tc) -> None:
    tc = make_tc(pin_alphabet="digits")
    s = tc_session(tc)
    m = tc_mint(tc, s["id"])
    assert re.fullmatch(r"\d{3}-\d{3}", m["pin"])
    assert redeem(tc, pin="XKD-MPT", code_id=m["code_id"]).status_code == 404
    assert redeem(tc, pin=m["pin"].replace("-", " "), code_id=m["code_id"]).status_code == 200


def test_precheck_public(tc: TestClient) -> None:
    r = TestClient(tc.app, base_url="https://testserver").get("/api/pair/precheck")
    assert r.status_code == 200
    assert r.json()["version"] and r.json()["server_time_ms"] > 1.7e12


def test_redeem_validation(tc: TestClient) -> None:
    assert redeem(tc, pin="").status_code == 400
    assert redeem(tc, pin="XKD-MPT", extra=1).status_code == 400
    assert redeem(tc, pin="XKD-MPT", device_label="x" * 61).status_code == 400


def test_concurrent_redeem_exactly_one_wins(make_settings) -> None:
    """Two threads redeem the same PIN at once: one 200, one 409 (G.2 step 5)."""
    app = create_app(make_settings())
    admin = TestClient(app, base_url="https://testserver", headers=CSRF)
    assert (
        admin.post(
            "/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
        ).status_code
        == 204
    )
    s = tc_session(admin)
    m = tc_mint(admin, s["id"])

    barrier = threading.Barrier(2)
    results: list[int] = []
    lock = threading.Lock()

    def attempt() -> None:
        client = TestClient(app, base_url="https://testserver")
        barrier.wait()
        r = client.post("/api/pair/redeem", json={"pin": m["pin"], "code_id": m["code_id"]})
        with lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == [200, 409], results
    assert len(admin.get(f"/api/sessions/{s['id']}").json()["cameras"]) == 1


@pytest.mark.parametrize("remember", [True, False])
def test_remember_flag_only_in_security_log(tc: TestClient, remember: bool) -> None:
    s = tc_session(tc)
    m = tc_mint(tc, s["id"])
    assert redeem(tc, pin=m["pin"], remember=remember).status_code == 200
    entry = next(e for e in tc.get("/api/security/log").json() if e["kind"] == "redeem_ok")
    assert entry["details"]["remember"] is remember
