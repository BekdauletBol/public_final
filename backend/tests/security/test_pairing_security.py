"""M2: redeem rate limits, PIN and token leakage (F-SEC-2, Section L)."""

from __future__ import annotations

import logging
import re

from fastapi.testclient import TestClient

from publicsafe.db.base import session_scope
from publicsafe.db.models import DeviceCredential, PairingCode
from tests.conftest import tc_mint, tc_session
from tests.integration.fake_phone import FakePhone


def test_redeem_rate_limit_10_per_minute_and_30_per_10_minutes(tc: TestClient) -> None:
    limiter = tc.app.state.limiters["redeem"]
    clock = {"t": 1000.0}
    limiter.clock = lambda: clock["t"]
    anon = TestClient(tc.app, base_url="https://testserver")
    total_allowed = 0
    for minute in range(4):
        clock["t"] = 1000.0 + minute * 61
        for _ in range(10):
            r = anon.post("/api/pair/redeem", json={"pin": "AAA-AAA"})
            if r.status_code != 429:
                total_allowed += 1
        r = anon.post("/api/pair/redeem", json={"pin": "AAA-AAA"})
        assert r.status_code == 429, minute
        assert r.json()["error"]["code"] == "rate_limited"
        assert int(r.headers["Retry-After"]) >= 1
    assert total_allowed == 30  # the 30 / 10 min rule capped minute 4


def test_pin_only_ever_in_the_mint_response(tc: TestClient, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    s = tc_session(tc)
    m = tc_mint(tc, s["id"])
    pin = m["pin"].replace("-", "")
    tc.post("/api/pair/redeem", json={"pin": "BBB-BBB", "code_id": m["code_id"]})
    tc.post("/api/pair/redeem", json={"pin": m["pin"], "code_id": m["code_id"]})
    for path in (
        f"/api/sessions/{s['id']}/pairing-codes",
        f"/api/sessions/{s['id']}",
        "/api/security/log",
        "/api/metrics",
        "/api/cameras",
    ):
        text = tc.get(path).text
        assert pin not in text and m["pin"] not in text, path
    assert pin not in caplog.text
    with session_scope(tc.app.state.sessionmaker) as db:
        code = db.get(PairingCode, m["code_id"])
        assert pin not in code.pin_hash and len(code.pin_hash) == 64


def test_device_token_never_logged_or_stored(tc: TestClient, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    s = tc_session(tc)
    m = tc_mint(tc, s["id"])
    r = tc.post("/api/pair/redeem", json={"pin": m["pin"], "code_id": m["code_id"]})
    token, camera_id = r.json()["device_token"], r.json()["camera_id"]
    with FakePhone(tc, token) as phone:
        phone.auth()
        phone.ping()
    tc.post(f"/api/cameras/{camera_id}/revoke", json={})
    with FakePhone(tc, token) as phone:
        phone.send({"type": "auth", "token": token, "client": {}})
        phone.expect_close()

    assert token not in caplog.text
    for path in ("/api/security/log", f"/api/cameras/{camera_id}", "/api/metrics"):
        assert token not in tc.get(path).text, path
    with session_scope(tc.app.state.sessionmaker) as db:
        creds = list(db.query(DeviceCredential).filter_by(camera_id=camera_id))
        assert creds and all(token not in c.token_hash for c in creds)
        assert all(re.fullmatch(r"[0-9a-f]{64}", c.token_hash) for c in creds)


def test_wrong_pin_response_identical_for_unknown_and_wrong(tc: TestClient) -> None:
    s = tc_session(tc)
    m = tc_mint(tc, s["id"])
    a = tc.post("/api/pair/redeem", json={"pin": "BBB-BBB", "code_id": m["code_id"]})
    b = tc.post(
        "/api/pair/redeem",
        json={"pin": "BBB-BBB", "code_id": "00000000-0000-4000-8000-000000000000"},
    )
    c = tc.post("/api/pair/redeem", json={"pin": "BBB-BBB"})
    assert a.status_code == b.status_code == c.status_code == 404
    assert a.json() == b.json() == c.json()
