"""M1: login rate limit, timing parity, cookie flags, security log (F-SEC-2/3, Section L)."""

from __future__ import annotations

import statistics
import time

import httpx
from fastapi import FastAPI

from tests.conftest import ADMIN_PASSWORD, ADMIN_USER


async def test_login_rate_limit_429_with_retry_after(client: httpx.AsyncClient) -> None:
    for _ in range(5):
        r = await client.post("/api/auth/login", json={"username": ADMIN_USER, "password": "bad"})
        assert r.status_code == 401
    r = await client.post(
        "/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    retry = int(r.headers["Retry-After"])
    assert 1 <= retry <= 60
    assert r.json()["error"]["details"]["retry_after_s"] == retry
    assert "set-cookie" not in r.headers


async def test_login_timing_unknown_user_vs_wrong_password(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """Median response times within 20 % of each other over 50 runs each."""
    limiter = app.state.limiters["login"]

    async def timed(username: str) -> float:
        limiter.reset()
        t0 = time.perf_counter()
        r = await client.post("/api/auth/login", json={"username": username, "password": "wrong"})
        assert r.status_code == 401
        return time.perf_counter() - t0

    for _ in range(5):  # warm-up
        await timed(ADMIN_USER)
    unknown = [await timed("nobody") for _ in range(50)]
    wrong = [await timed(ADMIN_USER) for _ in range(50)]
    a, b = statistics.median(unknown), statistics.median(wrong)
    assert abs(a - b) / max(a, b) < 0.20, f"unknown {a * 1000:.2f} ms vs wrong {b * 1000:.2f} ms"


async def test_security_log_records_logins(
    admin: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    await client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    rows = (await admin.get("/api/security/log")).json()
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "login_failed" and "login_ok" in kinds
    failed = rows[0]
    assert failed["ip"] and failed["details"] == {"username_length": 5}
    assert "ghost" not in str(rows) and ADMIN_PASSWORD not in str(rows)

    page = (await admin.get("/api/security/log", params={"limit": 1})).json()
    assert len(page) == 1
    nxt = (await admin.get("/api/security/log", params={"limit": 1, "cursor": page[0]["t"]})).json()
    assert not nxt or nxt[0]["id"] != page[0]["id"]
    assert (await admin.get("/api/security/log", params={"cursor": "junk"})).status_code == 400


async def test_password_hash_never_in_responses(admin: httpx.AsyncClient, app: FastAPI) -> None:
    secret = app.state.settings.admin_password_hash
    for path in ("/api/auth/me", "/api/sessions", "/api/security/log", "/api/health"):
        r = await admin.get(path)
        assert secret not in r.text
