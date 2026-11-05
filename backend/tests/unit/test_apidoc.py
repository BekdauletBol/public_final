"""M6: docs/API.md is generated from the code and must stay in sync with it."""

from __future__ import annotations

from publicsafe.apidoc import render_default
from publicsafe.config import ROOT_DIR


def test_api_md_matches_the_code() -> None:
    text = render_default()
    assert (ROOT_DIR / "docs" / "API.md").read_text(
        encoding="utf-8"
    ) == text, "docs/API.md is stale: run `make api-docs`"


def test_reference_covers_auth_and_websockets() -> None:
    text = render_default()
    assert "| POST | `/api/auth/login` | none |" in text
    assert "| GET | `/api/sessions` | admin |" in text
    assert "| `/api/test" not in text  # TEST_MODE routes are never documented as API
    assert "## WebSocket `/ws/camera`" in text and "## WebSocket `/ws/dashboard`" in text
