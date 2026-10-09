"""SQLAlchemy engine, session factory, declarative base and shared column types.

SQLite is the default (WAL, synchronous=NORMAL, foreign_keys=ON); PostgreSQL
is selected by ``DATABASE_URL=postgresql+psycopg://...``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, MetaData, String, create_engine, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from publicsafe.config import ROOT_DIR

#: Deterministic constraint names so Alembic can drop them (needed for SQLite batch mode).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: UUID4 as a 36-char string on SQLite, native UUID on PostgreSQL (returned as str).
UUIDStr = String(36).with_variant(postgresql.UUID(as_uuid=False), "postgresql")
#: JSON on SQLite, JSONB on PostgreSQL (Section K).
JSONVariant = JSON().with_variant(postgresql.JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[dt.datetime]):
    """Timezone-aware UTC datetimes on every backend (SQLite drops the offset)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Any) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use utcnow()")
        return value.astimezone(dt.UTC)

    def process_result_value(self, value: dt.datetime | None, dialect: Any) -> dt.datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def new_id() -> str:
    return str(uuid.uuid4())


def resolve_database_url(url: str, root: Path = ROOT_DIR) -> str:
    """Make a relative SQLite path absolute with respect to ``<root>``."""
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "sqlite"
        or not parsed.database
        or parsed.database == ":memory:"
    ):
        return url
    path = Path(parsed.database)
    if not path.is_absolute():
        path = root / path
    return str(parsed.set(database=str(path)))


def make_engine(url: str, *, echo: bool = False) -> Engine:
    """Create the engine with the backend-specific settings from Section K."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        in_memory = not parsed.database or parsed.database == ":memory:"
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if in_memory:
            kwargs["poolclass"] = StaticPool
        else:
            Path(parsed.database or "").parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, echo=echo, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            if not in_memory:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_engine(url, echo=echo, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success, roll back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
