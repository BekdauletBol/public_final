"""M1: AdminAuth password verification, cookie sessions and the .env writer."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from argon2.exceptions import InvalidHashError

from publicsafe.api.auth import AdminAuth, hash_token
from publicsafe.config import Settings, write_env_value
from publicsafe.db import migrate
from publicsafe.db.base import make_engine, make_sessionmaker, session_scope
from publicsafe.db.models import AdminSession
from tests.conftest import ADMIN_PASSWORD, ADMIN_USER


@pytest.fixture
def auth(admin_hash: str) -> AdminAuth:
    return AdminAuth(ADMIN_USER, admin_hash, dt.timedelta(hours=12))


def test_verify(auth: AdminAuth) -> None:
    assert auth.verify(ADMIN_USER, ADMIN_PASSWORD)
    assert not auth.verify(ADMIN_USER, "wrong")
    assert not auth.verify("nobody", ADMIN_PASSWORD)
    assert not auth.verify("", "")


def test_invalid_hash_rejected() -> None:
    with pytest.raises(InvalidHashError):
        AdminAuth(ADMIN_USER, "plain", dt.timedelta(hours=1))


def test_hash_token_is_sha256_hex() -> None:
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.fixture
def db(tmp_path: Path):
    url = f"sqlite+pysqlite:///{tmp_path / 'auth.db'}"
    migrate.upgrade(url)
    return make_sessionmaker(make_engine(url))


def test_session_lifecycle(auth: AdminAuth, db) -> None:
    with session_scope(db) as s:
        token, expires = auth.create_session(s, "127.0.0.1")
        assert len(token) >= 43  # 32 bytes urlsafe-b64
        assert expires - dt.datetime.now(dt.UTC) > dt.timedelta(hours=11, minutes=59)
    with session_scope(db) as s:
        row = auth.resolve(s, token)
        assert row is not None and row.id_hash == hash_token(token)
        assert s.get(AdminSession, token) is None  # plaintext id is never stored
        assert auth.resolve(s, "nope") is None
        assert auth.resolve(s, "") is None
    with session_scope(db) as s:
        auth.destroy(s, token)
    with session_scope(db) as s:
        assert auth.resolve(s, token) is None


def test_expired_session_is_removed(auth: AdminAuth, db) -> None:
    with session_scope(db) as s:
        token, _ = auth.create_session(s, None)
        row = s.get(AdminSession, hash_token(token))
        assert row is not None
        row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    with session_scope(db) as s:
        assert auth.resolve(s, token) is None
        assert s.get(AdminSession, hash_token(token)) is None


def test_idle_expiry_slides(auth: AdminAuth, db) -> None:
    with session_scope(db) as s:
        token, _ = auth.create_session(s, None)
        row = s.get(AdminSession, hash_token(token))
        assert row is not None
        old = dt.datetime.now(dt.UTC) - dt.timedelta(hours=6)
        row.last_seen_at = old
        row.expires_at = old + dt.timedelta(hours=12)
    with session_scope(db) as s:
        row = auth.resolve(s, token)
        assert row is not None
        assert row.expires_at - dt.datetime.now(dt.UTC) > dt.timedelta(hours=11, minutes=59)


def test_destroy_all(auth: AdminAuth, db) -> None:
    with session_scope(db) as s:
        auth.create_session(s, None)
        auth.create_session(s, None)
    with session_scope(db) as s:
        assert AdminAuth.destroy_all(s) == 2


def test_write_env_value_roundtrip(tmp_path: Path, admin_hash: str) -> None:
    example = tmp_path / ".env.example"
    example.write_text("# comment\nADMIN_USERNAME=admin\nADMIN_PASSWORD_HASH=\nPORT=8443\n")
    env = tmp_path / ".env"
    write_env_value(env, "ADMIN_PASSWORD_HASH", admin_hash)  # creates from the example
    lines = env.read_text().splitlines()
    assert lines[0] == "# comment" and "PORT=8443" in lines
    assert lines.count(f"ADMIN_PASSWORD_HASH='{admin_hash}'") == 1

    write_env_value(env, "ADMIN_PASSWORD_HASH", "x")  # replaces in place
    assert env.read_text().count("ADMIN_PASSWORD_HASH=") == 1
    write_env_value(env, "NEW_KEY", "v")  # appends
    assert env.read_text().endswith("NEW_KEY='v'\n")

    write_env_value(env, "ADMIN_PASSWORD_HASH", admin_hash)
    settings = Settings(_env_file=env)  # type: ignore[call-arg]
    assert settings.admin_password_hash == admin_hash  # '$' sequences survive dotenv parsing
    assert settings.port == 8443

    with pytest.raises(ValueError):
        write_env_value(env, "K", "it's")
