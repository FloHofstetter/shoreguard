"""Typer CLI for the Shoreguard server and management commands."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from shoreguard.services.registry import GatewayRegistry

logger = logging.getLogger(__name__)

cli = typer.Typer(
    name="shoreguard",
    help=(
        "Web control plane for NVIDIA OpenShell.\n\n"
        "Launch the Shoreguard dashboard to manage sandboxes, security policies, "
        "and approval flows through your browser.\n\n"
        "Connects to registered OpenShell gateways. "
        "Each gateway is addressed by name in the URL."
    ),
    no_args_is_help=False,
    add_completion=False,
)

# Register subcommand groups
from shoreguard.api.cli_audit import audit_app  # noqa: E402
from shoreguard.api.cli_config import config_app  # noqa: E402

cli.add_typer(config_app)
cli.add_typer(audit_app)


def _version_callback(value: bool) -> None:
    """Print version and exit when --version is passed.

    Args:
        value: True when ``--version`` flag is present.

    Raises:
        typer.Exit: After printing the version string.
    """
    if value:
        from shoreguard import __version__

        typer.echo(f"shoreguard {__version__}")
        raise typer.Exit


@cli.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
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
    no_auth: Annotated[
        bool,
        typer.Option(
            "--no-auth/--auth",
            envvar="SHOREGUARD_NO_AUTH",
            help="Disable authentication entirely (local development only).",
            rich_help_panel="Development",
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
    """Start the Shoreguard server.

    Args:
        ctx: Typer context — used to detect whether a subcommand was invoked.
        host: Network interface to listen on.
        port: HTTP port for the dashboard and REST API.
        log_level: Verbosity for Shoreguard and Uvicorn.
        reload: Auto-reload on code changes.
        local: Enable local mode with Docker lifecycle management.
        no_auth: Disable authentication entirely.
        database_url: SQLAlchemy database URL override.
        version: Print version and exit (handled by callback).
    """
    # If a subcommand (e.g. `shoreguard config show`) was invoked, the
    # callback still runs — but we must not start the server.
    if ctx.invoked_subcommand is not None:
        return

    import uvicorn

    from shoreguard.settings import get_settings, override_settings

    # Push CLI-resolved values into the Settings singleton so the rest of
    # the application (lifespan, services, routes) reads from one place.
    settings = get_settings()
    override_settings(
        settings.model_copy(
            update={
                "server": settings.server.model_copy(
                    update={
                        "host": host,
                        "port": port,
                        "log_level": log_level,
                        "reload": reload,
                        "local_mode": local,
                        "database_url": database_url or settings.server.database_url,
                    },
                ),
                "auth": settings.auth.model_copy(update={"no_auth": no_auth}),
            },
        ),
    )
    settings = get_settings()

    use_json = settings.server.log_format == "json"

    # Request-ID filter — must be attached to any handler that renders
    # %(request_id)s, otherwise the formatter raises KeyError.  The root
    # logger also gets it via shoreguard.api.main lifespan so propagated
    # records are covered, but attaching to the handler directly ensures
    # non-propagating loggers (e.g. uvicorn with propagate=True but its
    # own handlers) work too.
    from shoreguard.api.metrics import RequestIdFilter

    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    if use_json:
        from shoreguard.api.logging_config import JSONFormatter

        handler.setFormatter(JSONFormatter())
    else:
        _LOG_FORMAT = "%(asctime)s %(levelname)-5s [%(request_id)s] %(name)-20s  %(message)s"
        _LOG_DATE = "%H:%M:%S"

        class _ShortNameFormatter(logging.Formatter):
            """Strip the ``shoreguard.`` prefix from logger names."""

            def format(self, record: logging.LogRecord) -> str:
                if record.name.startswith("shoreguard."):
                    record.name = record.name.removeprefix("shoreguard.")
                return super().format(record)

        handler.setFormatter(_ShortNameFormatter(_LOG_FORMAT, datefmt=_LOG_DATE))

    logging.root.addHandler(handler)
    logging.root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if no_auth:
        logger.warning("Authentication DISABLED — do not use in production")
    if local:
        logger.info("Local mode enabled")
    if database_url:
        logger.info("Using database: %s", database_url.split("://")[0])

    # Unified log config for uvicorn so all output uses the same format.
    # In both JSON and text mode, let uvicorn logs propagate to the root
    # logger where our formatter + RequestIdFilter are installed.  This
    # ensures access logs carry the same request_id as application logs.
    _uvicorn_log_config: dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {},
        "loggers": {
            "uvicorn": {"level": log_level.upper(), "propagate": True},
            "uvicorn.error": {"level": log_level.upper(), "propagate": True},
            "uvicorn.access": {"level": log_level.upper(), "propagate": True},
        },
    }

    uvicorn.run(
        "shoreguard.api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
        log_config=_uvicorn_log_config,
        timeout_graceful_shutdown=settings.server.graceful_shutdown_timeout,
    )


def _import_filesystem_gateways(
    registry: GatewayRegistry,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Import gateways from openshell filesystem config into the DB registry.

    Gateways already in the DB are silently skipped.

    Args:
        registry: Gateway registry to import into.
        log_fn: Callback for status lines; falls back to module logger.

    Returns:
        tuple[int, int]: ``(imported, skipped)`` counts.
    """
    import json as json_mod
    from urllib.parse import urlparse

    from shoreguard.config import (
        ENDPOINT_RE as _ENDPOINT_RE,
    )
    from shoreguard.config import (
        VALID_GATEWAY_NAME_RE as _VALID_IMPORT_NAME_RE,
    )
    from shoreguard.config import is_private_ip, openshell_config_dir
    from shoreguard.settings import get_settings

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
        _max_cert = get_settings().limits.max_cert_bytes
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

        if is_private_ip(host) and not get_settings().server.local_mode:
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


def _cli_init_db(database_url: str | None) -> Engine:
    """Init DB + auth for CLI commands.

    Args:
        database_url: Optional database URL override.

    Returns:
        Engine: The initialised SQLAlchemy engine.  Callers must ``dispose()``
        it when done.
    """
    from sqlalchemy.orm import sessionmaker as sa_sessionmaker

    from shoreguard.api.auth import init_auth
    from shoreguard.db import init_db
    from shoreguard.settings import get_settings, override_settings

    if database_url:
        settings = get_settings()
        override_settings(
            settings.model_copy(
                update={
                    "server": settings.server.model_copy(
                        update={"database_url": database_url},
                    ),
                },
            ),
        )
    engine = init_db()
    init_auth(sa_sessionmaker(bind=engine))
    return engine


@cli.command("create-user")
def create_user_cmd(
    email: Annotated[str, typer.Argument(help="Email address for the new user")],
    role: Annotated[
        str,
        typer.Option("--role", help="Role: admin, operator, or viewer"),
    ] = "admin",
    password: Annotated[
        str | None,
        typer.Option("--password", help="Password (prompted if omitted)"),
    ] = None,
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="SHOREGUARD_DATABASE_URL",
            help="Database URL.",
        ),
    ] = None,
) -> None:
    """Create a user account (for initial setup or headless deployments).

    Args:
        email: Email address for the new user.
        role: Role: admin, operator, or viewer.
        password: Password (prompted if omitted).
        database_url: Optional database URL override.

    Raises:
        typer.Exit: On validation or database errors.
    """
    from shoreguard.api.auth import ROLES, create_user

    logging.basicConfig(level=logging.INFO)

    if role not in ROLES:
        typer.echo(f"Error: invalid role '{role}' (must be one of {ROLES})", err=True)
        raise typer.Exit(1)

    if password is None:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)

    try:
        engine = _cli_init_db(database_url)
    except Exception as e:
        typer.echo(f"Error: failed to initialise database: {e}", err=True)
        raise typer.Exit(1) from e

    try:
        info = create_user(email, password, role)
        typer.echo(f"User created: {info['email']} (role={info['role']})")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
    finally:
        engine.dispose()


_DB_URL_OPT = typer.Option(
    "--database-url",
    envvar="SHOREGUARD_DATABASE_URL",
    help="Database URL.",
)


@cli.command("list-users")
def list_users_cmd(
    database_url: Annotated[str | None, _DB_URL_OPT] = None,
) -> None:
    """List all user accounts.

    Args:
        database_url: Optional database URL override.
    """
    from shoreguard.api.auth import list_users

    logging.basicConfig(level=logging.WARNING)
    engine = _cli_init_db(database_url)
    try:
        users = list_users()
        if not users:
            typer.echo("No users found.")
            return
        for u in users:
            status = (
                "invited" if u["pending_invite"] else ("active" if u["is_active"] else "inactive")
            )
            typer.echo(f"  {u['email']:30s}  {u['role']:10s}  {status}")
    finally:
        engine.dispose()


@cli.command("delete-user")
def delete_user_cmd(
    email: Annotated[str, typer.Argument(help="Email of the user to delete")],
    database_url: Annotated[str | None, _DB_URL_OPT] = None,
) -> None:
    """Delete a user account by email.

    Args:
        email: Email of the user to delete.
        database_url: Optional database URL override.

    Raises:
        typer.Exit: If user not found or is last admin.
    """
    from shoreguard.api.auth import delete_user, list_users

    logging.basicConfig(level=logging.WARNING)
    engine = _cli_init_db(database_url)
    try:
        users = list_users()
        match = [u for u in users if u["email"] == email.strip().lower()]
        if not match:
            typer.echo(f"Error: no user with email '{email}'", err=True)
            raise typer.Exit(1)
        try:
            delete_user(match[0]["id"])
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)
        typer.echo(f"User '{match[0]['email']}' deleted.")
    finally:
        engine.dispose()


@cli.command("create-service-principal")
def create_sp_cmd(
    name: Annotated[str, typer.Argument(help="Name for the service principal")],
    role: Annotated[
        str,
        typer.Option("--role", help="Role: admin, operator, or viewer"),
    ] = "viewer",
    database_url: Annotated[str | None, _DB_URL_OPT] = None,
) -> None:
    """Create a service principal and print its API key.

    Args:
        name: Name for the service principal.
        role: Role: admin, operator, or viewer.
        database_url: Optional database URL override.

    Raises:
        typer.Exit: On validation or database errors.
    """
    from shoreguard.api.auth import ROLES, create_service_principal

    logging.basicConfig(level=logging.WARNING)
    if role not in ROLES:
        typer.echo(f"Error: invalid role '{role}' (must be one of {ROLES})", err=True)
        raise typer.Exit(1)
    engine = _cli_init_db(database_url)
    try:
        key, info = create_service_principal(name.strip(), role)
        typer.echo(f"Service principal created: {info['name']} (role={info['role']})")
        typer.echo(f"API key: {key}")
        typer.echo("Store this key securely — it will not be shown again.")
    except Exception as e:
        detail = str(e)
        if "UNIQUE" in detail or "unique" in detail.lower():
            detail = f"A service principal named '{name.strip()}' already exists"
        typer.echo(f"Error: {detail}", err=True)
        raise typer.Exit(1) from e
    finally:
        engine.dispose()


@cli.command("list-service-principals")
def list_sps_cmd(
    database_url: Annotated[str | None, _DB_URL_OPT] = None,
) -> None:
    """List all service principals.

    Args:
        database_url: Optional database URL override.
    """
    from shoreguard.api.auth import list_service_principals

    logging.basicConfig(level=logging.WARNING)
    engine = _cli_init_db(database_url)
    try:
        sps = list_service_principals()
        if not sps:
            typer.echo("No service principals found.")
            return
        for sp in sps:
            last = sp["last_used"] or "never"
            typer.echo(f"  {sp['name']:30s}  {sp['role']:10s}  last_used={last}")
    finally:
        engine.dispose()


@cli.command("import-gateways")
def import_gateways() -> None:
    """Import gateways from openshell filesystem config into the database.

    Raises:
        typer.Exit: If database initialisation fails.
    """
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
