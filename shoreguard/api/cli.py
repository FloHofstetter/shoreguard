"""Typer CLI for the Shoreguard server and management commands."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from shoreguard.services.registry import GatewayRegistry

logger = logging.getLogger(__name__)

cli = typer.Typer(
    name="shoreguard",
    help=(
        "Web control plane for NVIDIA OpenShell.\n\n"
        "Launch the Shoreguard dashboard to manage sandboxes, security policies, "
        "and approval flows through your browser.\n\n"
        "Connects to your active OpenShell gateway automatically "
        "via ~/.config/openshell/active_gateway."
    ),
    no_args_is_help=False,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    """Print version and exit when --version is passed."""
    if value:
        from shoreguard import __version__

        typer.echo(f"shoreguard {__version__}")
        raise typer.Exit


@cli.callback(invoke_without_command=True)
def main(
    host: Annotated[
        str,
        typer.Option(
            envvar="SHOREGUARD_HOST",
            help="Network interface to listen on. Use 127.0.0.1 for localhost only.",
            rich_help_panel="Server",
        ),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option(
            envvar="SHOREGUARD_PORT",
            help="HTTP port for the dashboard and REST API (/docs for Swagger UI).",
            rich_help_panel="Server",
        ),
    ] = 8888,
    log_level: Annotated[
        str,
        typer.Option(
            "--log-level",
            envvar="SHOREGUARD_LOG_LEVEL",
            help="Verbosity for Shoreguard and Uvicorn. Use 'debug' to troubleshoot.",
            rich_help_panel="Server",
        ),
    ] = "info",
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            envvar="SHOREGUARD_API_KEY",
            help="Shared API key for authentication. All API and UI access requires this key.",
            rich_help_panel="Security",
        ),
    ] = None,
    reload: Annotated[
        bool,
        typer.Option(
            "--reload/--no-reload",
            envvar="SHOREGUARD_RELOAD",
            help="Auto-reload on code changes. Disable with --no-reload for production.",
            rich_help_panel="Development",
        ),
    ] = True,
    local: Annotated[
        bool,
        typer.Option(
            "--local/--no-local",
            envvar="SHOREGUARD_LOCAL_MODE",
            help="Enable local mode: Docker lifecycle management for gateways.",
            rich_help_panel="Server",
        ),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="SHOREGUARD_DATABASE_URL",
            help="Database URL. Defaults to SQLite at ~/.config/shoreguard/shoreguard.db.",
            rich_help_panel="Server",
        ),
    ] = None,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    """Start the Shoreguard server."""
    import os

    import uvicorn

    from .auth import configure as configure_auth

    _LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)-20s  %(message)s"
    _LOG_DATE = "%H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format=_LOG_FORMAT,
        datefmt=_LOG_DATE,
    )
    # Shorten our own logger names: "shoreguard.api.main" → "api.main"
    for name in logging.root.manager.loggerDict:
        if name.startswith("shoreguard."):
            logging.getLogger(name).name = name.removeprefix("shoreguard.")

    # Propagate CLI flags to env so the lifespan picks them up
    if local:
        os.environ["SHOREGUARD_LOCAL_MODE"] = "1"
        logger.info("Local mode enabled")
    if database_url:
        os.environ["SHOREGUARD_DATABASE_URL"] = database_url
        logger.info("Using database: %s", database_url.split("://")[0])

    configure_auth(api_key)
    if not api_key:
        logger.info("No API key set — authentication disabled")

    # Unified log config for uvicorn so all output uses the same format
    _uvicorn_log_config: dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": _LOG_FORMAT, "datefmt": _LOG_DATE},
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": log_level.upper(), "propagate": False},
            "uvicorn.error": {"level": log_level.upper(), "propagate": False},
            "uvicorn.access": {
                "handlers": ["default"],
                "level": log_level.upper(),
                "propagate": False,
            },
        },
    }

    uvicorn.run(
        "shoreguard.api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
        log_config=_uvicorn_log_config,
        timeout_graceful_shutdown=5,
    )


def _import_filesystem_gateways(
    registry: GatewayRegistry,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Import gateways from openshell filesystem config into the DB registry.

    Returns (imported, skipped) counts.  Gateways already in the DB are
    silently skipped.  *log_fn* receives human-readable status lines; when
    ``None``, messages go to the module logger instead.
    """
    import json as json_mod
    import os
    from urllib.parse import urlparse

    from shoreguard.config import (
        ENDPOINT_RE as _ENDPOINT_RE,
    )
    from shoreguard.config import (
        VALID_GATEWAY_NAME_RE as _VALID_IMPORT_NAME_RE,
    )
    from shoreguard.config import is_private_ip, openshell_config_dir

    def _log(msg: str, *, level: int = logging.INFO) -> None:
        if log_fn is not None:
            log_fn(msg)
        else:
            logger.log(level, msg)

    gateways_dir = openshell_config_dir() / "gateways"
    if not gateways_dir.exists():
        _log(f"No filesystem gateways found at {gateways_dir}")
        return 0, 0

    imported = 0
    skipped = 0
    for entry in sorted(gateways_dir.iterdir()):
        if not entry.is_dir():
            continue
        metadata_file = entry / "metadata.json"
        if not metadata_file.exists():
            continue

        name = entry.name
        if not _VALID_IMPORT_NAME_RE.match(name):
            _log(f"  skip  {name} (invalid name format)")
            skipped += 1
            continue
        if registry.get(name) is not None:
            _log(f"  skip  {name} (already registered)")
            skipped += 1
            continue

        try:
            metadata = json_mod.loads(metadata_file.read_text())
        except (json_mod.JSONDecodeError, OSError) as e:
            _log(f"  error {name}: {e}", level=logging.WARNING)
            skipped += 1
            continue

        endpoint = metadata.get("gateway_endpoint", "")
        scheme = "https" if "https" in endpoint else "http"
        auth_mode = metadata.get("auth_mode")

        ca_cert = None
        client_cert = None
        client_key = None
        _max_cert = 65_536  # 64 KB — same limit as the API route
        mtls_dir = entry / "mtls"
        if mtls_dir.exists():
            ca_file = mtls_dir / "ca.crt"
            cert_file = mtls_dir / "tls.crt"
            key_file = mtls_dir / "tls.key"
            try:
                if ca_file.exists():
                    ca_cert = ca_file.read_bytes()
                if cert_file.exists():
                    client_cert = cert_file.read_bytes()
                if key_file.exists():
                    client_key = key_file.read_bytes()
            except OSError as e:
                _log(f"  error {name}: failed to read mTLS certs: {e}", level=logging.WARNING)
                skipped += 1
                continue
            cert_fields = [
                ("ca_cert", ca_cert),
                ("client_cert", client_cert),
                ("client_key", client_key),
            ]
            for label, blob in cert_fields:
                if blob is not None and len(blob) > _max_cert:
                    _log(
                        f"  skip  {name} ({label} exceeds {_max_cert} bytes)",
                        level=logging.WARNING,
                    )
                    skipped += 1
                    break
            else:
                # Only reached when no cert exceeded the limit (no break).
                pass
            if any(
                blob is not None and len(blob) > _max_cert
                for blob in (ca_cert, client_cert, client_key)
            ):
                continue

        meta = {
            "gpu": metadata.get("gpu", False),
            "is_remote": metadata.get("is_remote", False),
            "remote_host": metadata.get("remote_host"),
        }

        parsed = urlparse(endpoint)
        host = parsed.hostname
        if not host:
            _log(f"  skip  {name} (no hostname in endpoint '{endpoint}')")
            skipped += 1
            continue
        port = parsed.port or (443 if scheme == "https" else 80)
        clean_endpoint = f"{host}:{port}"

        if is_private_ip(host) and not os.environ.get("SHOREGUARD_LOCAL_MODE"):
            _log(f"  skip  {name} (private/loopback address: '{host}')", level=logging.WARNING)
            skipped += 1
            continue
        if not _ENDPOINT_RE.match(clean_endpoint):
            _log(f"  skip  {name} (invalid endpoint format: '{clean_endpoint}')")
            skipped += 1
            continue
        ep_port = int(clean_endpoint.rsplit(":", 1)[1])
        if ep_port < 1 or ep_port > 65535:
            _log(f"  skip  {name} (port out of range: {ep_port})")
            skipped += 1
            continue

        try:
            registry.register(
                name,
                clean_endpoint,
                scheme,
                auth_mode,
                ca_cert=ca_cert,
                client_cert=client_cert,
                client_key=client_key,
                metadata=meta,
            )
        except ValueError as e:
            _log(f"  error  {name}: {e}", level=logging.WARNING)
            skipped += 1
            continue
        except Exception as e:
            _log(f"  error  {name}: unexpected error: {e}", level=logging.ERROR)
            skipped += 1
            continue
        _log(f"  imported {name} ({clean_endpoint})")
        imported += 1

    return imported, skipped


@cli.command("import-gateways")
def import_gateways() -> None:
    """Import gateways from openshell filesystem config into the database."""
    from sqlalchemy.orm import sessionmaker as sa_sessionmaker

    from shoreguard.db import init_db
    from shoreguard.services.registry import GatewayRegistry

    logging.basicConfig(level=logging.INFO)

    try:
        engine = init_db()
    except Exception as e:
        typer.echo(f"Error: failed to initialise database: {e}", err=True)
        raise typer.Exit(1) from e

    try:
        factory = sa_sessionmaker(bind=engine)
        registry = GatewayRegistry(factory)
        imported, skipped = _import_filesystem_gateways(registry, log_fn=typer.echo)
        typer.echo(f"\nDone: {imported} imported, {skipped} skipped.")
    finally:
        engine.dispose()
