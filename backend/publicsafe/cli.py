"""``publicsafe`` command line: run, certs (M0); admin, eval, loadtest in later milestones."""

from __future__ import annotations

from pathlib import Path

import typer

from publicsafe import __version__
from publicsafe.config import ROOT_DIR, ConfigError, get_settings, startup_checks

app = typer.Typer(
    help="public.safe - smartphones as camera nodes for danger-zone monitoring.",
    no_args_is_help=True,
    add_completion=False,
)


def _fail(message: str, code: int = 2) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def run(
    host: str | None = typer.Option(None, help="Bind address (default: HOST from .env)."),
    port: int | None = typer.Option(None, help="Port (default: PORT from .env, 8443)."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (development)."),
    no_tls: bool = typer.Option(
        False, help="Serve plain HTTP (only for tooling; phones need HTTPS)."
    ),
) -> None:
    """Start the HTTPS server (uvicorn) after the startup checks."""
    import uvicorn

    settings = get_settings()
    try:
        warnings = startup_checks(settings, require_tls=not no_tls)
    except ConfigError as exc:
        raise _fail(f"Refusing to start:\n{exc}") from None
    for message in warnings:
        typer.secho(f"warning: {message}", fg=typer.colors.YELLOW, err=True)

    scheme = "http" if no_tls else "https"
    bind_host = host or settings.host
    bind_port = port or settings.port
    typer.echo(f"public.safe {__version__} listening on {scheme}://{bind_host}:{bind_port}")
    uvicorn.run(
        "publicsafe.main:create_app",
        factory=True,
        host=bind_host,
        port=bind_port,
        reload=reload,
        reload_dirs=[str(Path(__file__).resolve().parent)] if reload else None,
        ssl_certfile=None if no_tls else str(settings.ssl_certfile),
        ssl_keyfile=None if no_tls else str(settings.ssl_keyfile),
        log_level=settings.log_level.lower(),
        ws="auto",
    )


@app.command()
def certs(
    force: bool = typer.Option(False, help="Re-issue even if certs/dev.pem exists."),
    certs_dir: Path = typer.Option(ROOT_DIR / "certs", help="Output directory."),
) -> None:
    """Create the local HTTPS certificate with mkcert and print the phone CA steps."""
    from publicsafe.certs import CertsError, ensure_certs, phone_ca_instructions

    try:
        result = ensure_certs(certs_dir, force=force)
    except CertsError as exc:
        raise _fail(str(exc)) from None

    for message in result.messages:
        typer.secho(f"note: {message}", fg=typer.colors.YELLOW)
    verb = "Issued" if result.created else "Found"
    typer.echo(f"{verb} {result.cert} and {result.key}")
    typer.echo("Names: " + ", ".join(result.names))
    typer.echo("")
    typer.echo(phone_ca_instructions(result.caroot))
    typer.echo("Full guide: docs/PHONE_SETUP.md (served at https://<host>:8443/help/phone)")


if __name__ == "__main__":
    app()


admin_app = typer.Typer(help="Admin account management.", no_args_is_help=True)
app.add_typer(admin_app, name="admin")

MIN_PASSWORD_LENGTH = 8


@admin_app.command("set-password")
def admin_set_password(
    password_stdin: bool = typer.Option(
        False, help="Read the password from stdin (first line) instead of prompting."
    ),
    env_file: Path = typer.Option(ROOT_DIR / ".env", help="Dotenv file to update."),
) -> None:
    """Choose the admin password: writes ADMIN_PASSWORD_HASH (argon2id) and logs out all admins."""
    import sys

    from argon2 import PasswordHasher

    from publicsafe.api.auth import AdminAuth
    from publicsafe.config import write_env_value
    from publicsafe.db.base import (
        make_engine,
        make_sessionmaker,
        resolve_database_url,
        session_scope,
    )

    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = typer.prompt("New admin password", hide_input=True, confirmation_prompt=True)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise _fail(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    write_env_value(env_file, "ADMIN_PASSWORD_HASH", PasswordHasher().hash(password))
    typer.echo(f"Wrote ADMIN_PASSWORD_HASH to {env_file}")

    settings = get_settings()
    try:
        engine = make_engine(resolve_database_url(settings.database_url))
        with session_scope(make_sessionmaker(engine)) as db:
            removed = AdminAuth.destroy_all(db)
        typer.echo(f"Invalidated {removed} admin session(s).")
    except Exception as exc:
        typer.secho(f"note: no admin sessions invalidated ({type(exc).__name__})", fg="yellow")
    typer.echo("Restart the server for the new password to take effect.")
