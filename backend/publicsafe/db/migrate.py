"""Programmatic Alembic: upgrade at startup (AUTO_MIGRATE) or verify the schema is current."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


class SchemaMismatchError(RuntimeError):
    """The database schema is not at the migration head and AUTO_MIGRATE is off."""


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def ensure_current(engine: Engine, url: str) -> None:
    head = head_revision()
    current = current_revision(engine)
    if current != head:
        raise SchemaMismatchError(
            f"Database schema is at revision {current or 'none'}, expected {head}. "
            "Run `cd backend && ../.venv/bin/alembic upgrade head` or set AUTO_MIGRATE=true."
        )
