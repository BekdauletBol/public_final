"""Runtime configuration for public.safe.

Every environment variable the application reads is declared here as a field of
:class:`Settings` and mirrored, with a comment, in ``<root>/.env.example``.
Keep the two in sync in the same milestone.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root (``<root>`` in the specification).
ROOT_DIR: Path = Path(__file__).resolve().parents[2]
#: Directory holding the Markdown docs (served rendered at ``/help/...``).
DOCS_DIR: Path = ROOT_DIR / "docs"
#: Command the operator must run to create the admin password hash.
SET_PASSWORD_COMMAND = "publicsafe admin set-password"


class ConfigError(RuntimeError):
    """The configuration cannot support starting the server (human-readable message)."""


class Settings(BaseSettings):
    """All configuration, read from the environment and ``<root>/.env``."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application ---------------------------------------------------------
    app_env: Literal["development", "production", "test"] = "development"
    host: str = "0.0.0.0"
    port: int = Field(default=8443, ge=1, le=65535)
    ssl_certfile: Path = ROOT_DIR / "certs" / "dev.pem"
    ssl_keyfile: Path = ROOT_DIR / "certs" / "dev-key.pem"
    public_url: str | None = None
    frontend_dist: Path = ROOT_DIR / "frontend" / "dist"
    data_dir: Path = ROOT_DIR / "data"
    log_level: str = "INFO"
    log_json: bool = True

    # --- admin ---------------------------------------------------------------
    admin_username: str = Field(default="admin", min_length=1, max_length=80)
    admin_password_hash: str | None = None
    session_idle_hours: float = Field(default=12.0, gt=0)

    # --- database ------------------------------------------------------------
    database_url: str = "sqlite+pysqlite:///data/publicsafe.db"
    auto_migrate: bool = True

    # --- pairing (Section G) -------------------------------------------------
    pin_alphabet: Literal["letters", "digits"] = "letters"
    pin_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    pin_max_attempts: int = Field(default=5, ge=1, le=20)

    # --- inference and scheduling (Section H) --------------------------------
    model_path: str = "yolov8n.pt"
    inference_device: str | None = None
    inference_half: bool = False
    inference_imgsz: int = Field(default=640, ge=160, le=1920)
    scheduler_strategy: Literal["latest", "fifo"] = "latest"
    max_frame_bytes: int = Field(default=1_500_000, ge=10_000)

    # --- modes ---------------------------------------------------------------
    eval_record: bool = False
    test_mode: bool = False

    @field_validator("admin_password_hash", "public_url", "inference_device", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """Treat an empty value in ``.env`` (``KEY=``) as unset."""
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @property
    def log_level_name(self) -> str:
        return self.log_level.upper()


def startup_checks(settings: Settings, *, require_tls: bool = True) -> list[str]:
    """Validate that the server may start.

    Returns a list of warnings to print. Raises :class:`ConfigError` with a
    message that tells the operator exactly what to do when the server must
    refuse to start (F-ADM-1, TEST_MODE in production, missing certificates).
    """
    problems: list[str] = []
    warnings: list[str] = []

    if not settings.admin_password_hash:
        problems.append(
            "ADMIN_PASSWORD_HASH is not set. "
            f"Run `{SET_PASSWORD_COMMAND}` to choose the admin password; "
            "it writes the argon2id hash to .env."
        )
    if settings.test_mode and settings.app_env == "production":
        problems.append("TEST_MODE=1 is refused when APP_ENV=production.")
    if require_tls:
        for name, path in (
            ("SSL_CERTFILE", settings.ssl_certfile),
            ("SSL_KEYFILE", settings.ssl_keyfile),
        ):
            if not path.is_file():
                problems.append(f"{name} not found at {path}. Run `make certs` to create it.")

    if settings.test_mode:
        warnings.append("TEST_MODE=1: test-only endpoints (/api/test/*) are enabled.")
    if settings.scheduler_strategy == "fifo":
        warnings.append(
            "SCHEDULER_STRATEGY=fifo is for experiments only (unbounded shared queue); "
            "use `latest` for normal operation."
        )
    if settings.eval_record:
        warnings.append("EVAL_RECORD=1: every processed frame is written to data/recordings/.")

    if problems:
        raise ConfigError("\n".join(problems))
    return warnings


def write_env_value(env_file: Path, key: str, value: str, template: Path | None = None) -> None:
    """Set ``KEY='value'`` in a dotenv file, replacing an existing line or appending.

    The file is created from ``template`` (``.env.example``) when missing. The
    value is single-quoted so that ``$`` (as in argon2 hashes) is never expanded.
    """
    if "'" in value or "\n" in value:
        raise ValueError("value must not contain quotes or newlines")
    if not env_file.exists():
        source = template if template is not None else env_file.with_name(".env.example")
        env_file.write_text(source.read_text(encoding="utf-8") if source.exists() else "")
    lines = env_file.read_text(encoding="utf-8").splitlines()
    new_line = f"{key}='{value}'"
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}="):
            lines[index] = new_line
            replaced = True
    if not replaced:
        lines.append(new_line)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings instance (cached)."""
    return Settings()
