"""M1: Alembic upgrade/downgrade round-trip on SQLite and (when available) PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from publicsafe.db import (
    migrate,
    models,  # noqa: F401
)
from publicsafe.db.base import Base, make_engine

EXPECTED_TABLES = {
    "sessions",
    "cameras",
    "device_credentials",
    "pairing_codes",
    "zones",
    "events",
    "metric_samples",
    "admin_sessions",
    "security_log",
}

PG_URL = os.environ.get("PUBLICSAFE_TEST_PG_URL")


def _roundtrip(url: str) -> None:
    migrate.upgrade(url)
    engine = make_engine(url)
    try:
        names = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert names == EXPECTED_TABLES
        assert migrate.current_revision(engine) == migrate.head_revision()
        # models and migrations agree (what `alembic check` verifies)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(ctx, Base.metadata)
        assert diff == [], diff
        migrate.ensure_current(engine, url)

        migrate.downgrade(url)
        assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set()
        with pytest.raises(migrate.SchemaMismatchError):
            migrate.ensure_current(engine, url)

        migrate.upgrade(url)
        assert set(inspect(engine).get_table_names()) - {"alembic_version"} == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_sqlite_roundtrip(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'mig.db'}"
    _roundtrip(url)
    engine = make_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def test_sqlite_cascade_delete(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'fk.db'}"
    migrate.upgrade(url)
    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO sessions (id, name, created_at) VALUES ('s', 'n', '2026-01-01')")
        )
        conn.execute(
            text(
                "INSERT INTO cameras (id, session_id, name, status, settings, created_at) "
                "VALUES ('c', 's', 'cam', 'paired', '{}', '2026-01-01')"
            )
        )
        conn.execute(text("DELETE FROM sessions WHERE id = 's'"))
        assert conn.execute(text("SELECT COUNT(*) FROM cameras")).scalar() == 0
    engine.dispose()


@pytest.mark.skipif(not PG_URL, reason="set PUBLICSAFE_TEST_PG_URL (docker compose up -d db)")
def test_postgres_roundtrip() -> None:
    assert PG_URL is not None
    engine = make_engine(PG_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    engine.dispose()
    _roundtrip(PG_URL)
    engine = make_engine(PG_URL)
    with engine.connect() as conn:
        kind = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='zones' AND column_name='points'"
            )
        ).scalar()
        assert kind == "jsonb"
        kind = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='cameras' AND column_name='id'"
            )
        ).scalar()
        assert kind == "uuid"
    engine.dispose()
    command.stamp  # noqa: B018 - keeps the import used for readers
