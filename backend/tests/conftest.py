"""Shared fixtures. Tests never read the developer's real .env or shell environment."""

from __future__ import annotations

import pytest

from publicsafe.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every Settings variable from the environment before each test."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def make_settings():
    """Build a Settings object that ignores .env; keyword overrides apply."""

    def _make(**overrides: object) -> Settings:
        return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]

    return _make
