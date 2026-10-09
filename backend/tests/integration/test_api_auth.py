"""M1: login/logout/me, cookie flags, CSRF header (F-ADM-2, F-SEC-1)."""

from __future__ import annotations

import httpx

from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, CSRF


async def test_login_sets_hardened_cookie(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("ps_admin=")
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=43200"):
        assert flag.lower() in cookie.lower(), cookie
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == ADMIN_USER
    assert me.json()["session_expires_at"].endswith("Z")


async def test_wrong_password_and_unknown_user_same_response(client: httpx.AsyncClient) -> None:
    a = await client.post("/api/auth/login", json={"username": ADMIN_USER, "password": "nope"})
    b = await client.post("/api/auth/login", json={"username": "ghost", "password": ADMIN_PASSWORD})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()
    assert a.json()["error"]["code"] == "invalid_credentials"
    assert "set-cookie" not in a.headers


async def test_login_validation(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/login", json={"username": "", "password": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"
    assert "username" in r.json()["error"]["details"]["fields"]


async def test_unauthenticated_is_401(client: httpx.AsyncClient) -> None:
    for path in ("/api/auth/me", "/api/sessions", "/api/cameras", "/api/security/log"):
        r = await client.get(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "unauthenticated"
    r = await client.post("/api/sessions", json={"name": "x"}, headers=CSRF)
    assert r.status_code == 401


async def test_csrf_header_required_on_non_get(client: httpx.AsyncClient) -> None:
    await client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    r = await client.post("/api/sessions", json={"name": "x"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"
    assert (await client.get("/api/sessions")).status_code == 200  # GET needs no header
    r = await client.post("/api/sessions", json={"name": "x"}, headers=CSRF)
    assert r.status_code == 201


async def test_logout_invalidates_session(admin: httpx.AsyncClient) -> None:
    assert (await admin.post("/api/auth/logout")).status_code == 204
    assert (await admin.get("/api/auth/me")).status_code == 401


async def test_forged_cookie_rejected(client: httpx.AsyncClient) -> None:
    client.cookies.set("ps_admin", "A" * 43)
    assert (await client.get("/api/auth/me")).status_code == 401
