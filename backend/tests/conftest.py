"""Shared fixtures. Tests never read the developer's real .env or shell environment."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI

from publicsafe.config import Settings
from publicsafe.main import create_app

ADMIN_USER = "admin"
ADMIN_PASSWORD = "correct horse battery"
#: Low-cost argon2id parameters keep the suite fast; the real CLI uses the library defaults.
FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)
CSRF = {"X-Requested-With": "publicsafe"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every Settings variable from the environment before each test."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture(scope="session")
def admin_hash() -> str:
    return FAST_HASHER.hash(ADMIN_PASSWORD)


@pytest.fixture
def make_settings(tmp_path: Path, admin_hash: str) -> Callable[..., Settings]:
    """Settings that ignore .env, use a temp SQLite file and no built frontend."""

    def _make(**overrides: object) -> Settings:
        base: dict[str, object] = {
            "admin_password_hash": admin_hash,
            "admin_username": ADMIN_USER,
            "database_url": f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
            "frontend_dist": tmp_path / "no-dist",
            "data_dir": tmp_path / "data",
            "test_mode": True,
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)  # type: ignore[call-arg]

    return _make


@pytest.fixture
def app(make_settings: Callable[..., Settings]) -> FastAPI:
    return create_app(make_settings())


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Anonymous client. https base URL so the Secure cookie is accepted."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


@pytest.fixture
async def admin(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Logged-in client that always sends the CSRF header."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testserver", headers=CSRF
    ) as c:
        r = await c.post(
            "/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
        )
        assert r.status_code == 204, r.text
        yield c


async def make_session(admin: httpx.AsyncClient, name: str = "Main square") -> dict:
    r = await admin.post("/api/sessions", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def make_camera(admin: httpx.AsyncClient, session_id: str, name: str = "cam") -> dict:
    r = await admin.post("/api/test/cameras", json={"session_id": session_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


SQUARE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]


async def make_zone(admin: httpx.AsyncClient, camera_id: str, **overrides: object) -> dict:
    body: dict[str, object] = {
        "name": "crossing",
        "points": SQUARE,
        "frame_w": 1280,
        "frame_h": 720,
    }
    body.update(overrides)
    r = await admin.post(f"/api/cameras/{camera_id}/zones", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# --- synchronous client with lifespan (WebSocket tests) ----------------------


@pytest.fixture
def make_tc(make_settings: Callable[..., Settings]):
    """Logged-in TestClient whose context runs the lifespan (sweeper, metrics)."""
    from fastapi.testclient import TestClient

    opened: list[TestClient] = []

    def _make(**overrides: object) -> TestClient:
        tc = TestClient(create_app(make_settings(**overrides)), base_url="https://testserver")
        tc.__enter__()
        opened.append(tc)
        tc.headers.update(CSRF)
        r = tc.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
        assert r.status_code == 204, r.text
        return tc

    yield _make
    for tc in opened:
        tc.__exit__(None, None, None)


@pytest.fixture
def tc(make_tc):
    return make_tc()


def tc_session(tc, name: str = "Main square") -> dict:
    r = tc.post("/api/sessions", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def tc_camera(tc, session_id: str, name: str = "cam") -> dict:
    r = tc.post("/api/test/cameras", json={"session_id": session_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def tc_mint(tc, session_id: str, **body: object) -> dict:
    r = tc.post(f"/api/sessions/{session_id}/pairing-codes", json=body)
    assert r.status_code == 201, r.text
    return r.json()
