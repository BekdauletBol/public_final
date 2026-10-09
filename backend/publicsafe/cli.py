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
