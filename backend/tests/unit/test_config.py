"""M0: environment parsing and the startup refusal when ADMIN_PASSWORD_HASH is missing."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from publicsafe.config import SET_PASSWORD_COMMAND, ConfigError, startup_checks


def test_defaults(make_settings) -> None:
    s = make_settings()
    assert s.port == 8443
    assert s.app_env == "development"
    assert s.pin_alphabet == "letters"
    assert s.pin_ttl_seconds == 300
    assert s.pin_max_attempts == 5
    assert s.scheduler_strategy == "latest"
    assert s.max_frame_bytes == 1_500_000
    assert s.admin_password_hash is None
    assert s.test_mode is False and s.eval_record is False


def test_env_parsing(monkeypatch: pytest.MonkeyPatch, make_settings) -> None:
    monkeypatch.setenv("PORT", "9443")
    monkeypatch.setenv("SCHEDULER_STRATEGY", "fifo")
    monkeypatch.setenv("TEST_MODE", "1")
    monkeypatch.setenv("PIN_ALPHABET", "digits")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "$argon2id$v=19$m=65536,t=3,p=4$abc$def")
    monkeypatch.setenv("inference_device", "cpu")  # case-insensitive
    s = make_settings()
    assert s.port == 9443
    assert s.scheduler_strategy == "fifo"
    assert s.test_mode is True
    assert s.pin_alphabet == "digits"
    assert s.admin_password_hash.startswith("$argon2id$")
    assert s.inference_device == "cpu"


def test_blank_values_are_unset(monkeypatch: pytest.MonkeyPatch, make_settings) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "")
    monkeypatch.setenv("INFERENCE_DEVICE", "  ")
    s = make_settings()
    assert s.admin_password_hash is None
    assert s.inference_device is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PORT", "70000"),
        ("PORT", "zero"),
        ("SCHEDULER_STRATEGY", "random"),
        ("PIN_ALPHABET", "emoji"),
        ("PIN_TTL_SECONDS", "5"),
        ("APP_ENV", "staging"),
    ],
)
def test_invalid_values_rejected(
    monkeypatch: pytest.MonkeyPatch, make_settings, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        make_settings()


def _certs(tmp_path: Path) -> dict[str, Path]:
    cert, key = tmp_path / "dev.pem", tmp_path / "dev-key.pem"
    cert.write_text("cert")
    key.write_text("key")
    return {"ssl_certfile": cert, "ssl_keyfile": key}


def test_missing_admin_hash_refuses_start_with_clear_message(tmp_path: Path, make_settings) -> None:
    s = make_settings(admin_password_hash=None, **_certs(tmp_path))
    with pytest.raises(ConfigError) as excinfo:
        startup_checks(s)
    message = str(excinfo.value)
    assert "ADMIN_PASSWORD_HASH" in message
    assert SET_PASSWORD_COMMAND in message


def test_missing_certs_refuses_start(tmp_path: Path, make_settings) -> None:
    s = make_settings(
        admin_password_hash="h",
        ssl_certfile=tmp_path / "no.pem",
        ssl_keyfile=tmp_path / "no-key.pem",
    )
    with pytest.raises(ConfigError) as excinfo:
        startup_checks(s)
    assert "make certs" in str(excinfo.value)
    assert "SSL_CERTFILE" in str(excinfo.value)
    assert "SSL_KEYFILE" in str(excinfo.value)
    # without TLS requirement the same settings are fine
    assert startup_checks(s, require_tls=False) == []


def test_test_mode_refused_in_production(tmp_path: Path, make_settings) -> None:
    s = make_settings(
        admin_password_hash="h", test_mode=True, app_env="production", **_certs(tmp_path)
    )
    with pytest.raises(ConfigError, match="TEST_MODE"):
        startup_checks(s)


def test_startup_warnings(tmp_path: Path, make_settings) -> None:
    s = make_settings(
        admin_password_hash="h",
        test_mode=True,
        scheduler_strategy="fifo",
        eval_record=True,
        **_certs(tmp_path),
    )
    warnings = startup_checks(s)
    assert any("TEST_MODE" in w for w in warnings)
    assert any("fifo" in w for w in warnings)
    assert any("EVAL_RECORD" in w for w in warnings)
    assert len(warnings) == 3
