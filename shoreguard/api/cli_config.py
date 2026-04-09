"""``shoreguard config`` CLI subcommands.

Introspects the Pydantic Settings tree and emits it in several formats
(table / json / env / markdown) so operators can see exactly which
``SHOREGUARD_*`` environment variables exist, their current effective
values, and their documented purpose.
"""

from __future__ import annotations

import json as json_mod
from typing import Annotated, Any

import typer
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings

config_app = typer.Typer(
    name="config",
    help="Inspect the ShoreGuard configuration tree.",
    no_args_is_help=True,
)

# Fields whose values must be redacted unless --show-sensitive is passed.
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "secret_key",
        "admin_password",
        "client_secret",
        "password",
    }
)
REDACTED = "***REDACTED***"


def _env_prefix(model: type[BaseSettings]) -> str:
    """Return the ``env_prefix`` configured on a settings sub-model.

    Args:
        model: The settings sub-model class to inspect.

    Returns:
        str: The configured ``env_prefix`` string, or an empty string if none.
    """
    mc = getattr(model, "model_config", None)
    if mc is None:
        return ""
    if isinstance(mc, dict):
        return mc.get("env_prefix", "") or ""
    return getattr(mc, "env_prefix", "") or ""


def _is_default(field_info: FieldInfo, current: Any) -> bool:
    """Check whether *current* equals the Field default (handling factories).

    Args:
        field_info: The Pydantic field metadata describing the default.
        current: The current effective value to compare against the default.

    Returns:
        bool: ``True`` if *current* equals the field default, ``False`` otherwise.
    """
    if field_info.default_factory is not None:
        try:
            return field_info.default_factory() == current  # type: ignore[call-arg]
        except Exception:  # noqa: BLE001
            return False
    return field_info.default == current


def _format_value(value: Any) -> str:
    """Render a scalar/list value for the table and env formats.

    Args:
        value: The value to render.

    Returns:
        str: The rendered string representation of *value*.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json_mod.dumps(value)
    return str(value)


def _iter_fields(
    settings: BaseSettings,
    *,
    section_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Walk the root ``Settings`` instance and yield one row per leaf field.

    Each row contains: ``section``, ``field``, ``env_var``, ``value``,
    ``default``, ``is_default``, ``description``, ``type``, ``sensitive``.

    Args:
        settings: The root settings instance to walk.
        section_filter: If given, only include fields from this section.

    Returns:
        list[dict[str, Any]]: One dict per leaf field, as described above.
    """
    rows: list[dict[str, Any]] = []
    # Access model_fields on the class, not the instance (Pydantic v2 deprecation).
    root_fields = type(settings).model_fields
    for section_name, section_info in root_fields.items():
        if section_filter and section_name != section_filter:
            continue
        sub: BaseSettings = getattr(settings, section_name)
        if not isinstance(sub, BaseSettings):
            continue
        prefix = _env_prefix(type(sub))
        sub_fields = type(sub).model_fields
        for field_name, field_info in sub_fields.items():
            env_var = f"{prefix}{field_name.upper()}"
            current = getattr(sub, field_name)
            default: Any
            if field_info.default_factory is not None:
                try:
                    default = field_info.default_factory()  # type: ignore[call-arg]
                except Exception:  # noqa: BLE001
                    default = None
            else:
                default = field_info.default
            sensitive = field_name in SENSITIVE_FIELD_NAMES
            rows.append(
                {
                    "section": section_name,
                    "field": field_name,
                    "env_var": env_var,
                    "value": current,
                    "default": default,
                    "is_default": _is_default(field_info, current),
                    "description": field_info.description or "",
                    "type": str(field_info.annotation) if field_info.annotation else "",
                    "sensitive": sensitive,
                }
            )
        _ = section_info  # unused
    return rows


def _redact_rows(rows: list[dict[str, Any]], *, show_sensitive: bool) -> None:
    """Mutate rows in-place to redact sensitive values unless allowed.

    Args:
        rows: The rows to redact in-place.
        show_sensitive: If ``True``, leave sensitive values untouched.
    """
    if show_sensitive:
        return
    for row in rows:
        if row["sensitive"] and row["value"] not in (None, ""):
            row["value"] = REDACTED


def _print_table(rows: list[dict[str, Any]]) -> None:
    """Emit rows as a plain-text table (no Rich dependency).

    Args:
        rows: The rows to render.
    """
    headers = ["ENV_VAR", "VALUE", "DEFAULT?", "DESCRIPTION"]
    data = [
        [
            row["env_var"],
            _format_value(row["value"]),
            "yes" if row["is_default"] else "no",
            row["description"],
        ]
        for row in rows
    ]
    widths = [max(len(headers[i]), max((len(r[i]) for r in data), default=0)) for i in range(4)]
    # Cap description width so the table stays readable.
    widths[3] = min(widths[3], 70)

    def _fmt(row: list[str]) -> str:
        cells = []
        for i, cell in enumerate(row):
            if i == 3 and len(cell) > widths[3]:
                cell = cell[: widths[3] - 1] + "…"
            cells.append(cell.ljust(widths[i]))
        return "  ".join(cells).rstrip()

    typer.echo(_fmt(headers))
    typer.echo("  ".join("-" * w for w in widths))
    for row in data:
        typer.echo(_fmt(row))


def _print_json(rows: list[dict[str, Any]]) -> None:
    """Emit rows as a JSON object keyed by env var name.

    Args:
        rows: The rows to render.
    """
    out = {
        row["env_var"]: {
            "value": row["value"],
            "default": row["default"],
            "is_default": row["is_default"],
            "description": row["description"],
            "section": row["section"],
            "field": row["field"],
        }
        for row in rows
    }
    typer.echo(json_mod.dumps(out, indent=2, default=str))


def _print_env(rows: list[dict[str, Any]]) -> None:
    """Emit rows as .env-style lines with descriptions as comments.

    Args:
        rows: The rows to render.
    """
    for row in rows:
        if row["description"]:
            typer.echo(f"# {row['description']}")
        typer.echo(f"{row['env_var']}={_format_value(row['value'])}")
        typer.echo("")


def _print_markdown(rows: list[dict[str, Any]]) -> None:
    """Emit rows as a Markdown reference document grouped by section.

    Args:
        rows: The rows to render.
    """
    typer.echo("# ShoreGuard Settings Reference")
    typer.echo("")
    typer.echo(
        "Auto-generated from `shoreguard config schema --format markdown`. "
        "Every environment variable understood by ShoreGuard is listed below, "
        "grouped by the settings sub-model it belongs to."
    )
    typer.echo("")
    current_section: str | None = None
    for row in rows:
        if row["section"] != current_section:
            current_section = row["section"]
            typer.echo(f"## `{current_section}`")
            typer.echo("")
            typer.echo("| Environment variable | Default | Description |")
            typer.echo("|---|---|---|")
        default_str = _format_value(row["default"])
        if len(default_str) > 40:
            default_str = default_str[:37] + "..."
        desc = row["description"] or ""
        # Escape pipes in description
        desc = desc.replace("|", "\\|")
        typer.echo(f"| `{row['env_var']}` | `{default_str}` | {desc} |")
    typer.echo("")


def _collect_rows(
    section: str | None, *, show_sensitive: bool, schema_only: bool
) -> list[dict[str, Any]]:
    from shoreguard.settings import get_settings

    settings = get_settings()
    rows = _iter_fields(settings, section_filter=section)
    if schema_only:
        # Replace values with defaults so effective config doesn't leak.
        for row in rows:
            row["value"] = row["default"]
            row["is_default"] = True
    _redact_rows(rows, show_sensitive=show_sensitive)
    return rows


_FORMAT_HELP = "Output format: table | json | env | markdown"


@config_app.command("show")
def config_show(
    section: Annotated[
        str | None,
        typer.Argument(
            help="Filter to a single section (e.g. 'auth', 'database', 'oidc')",
        ),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help=_FORMAT_HELP),
    ] = "table",
    show_sensitive: Annotated[
        bool,
        typer.Option(
            "--show-sensitive",
            help="Print secret values in plain text (default: redacted)",
        ),
    ] = False,
) -> None:
    """Print the current effective configuration.

    Args:
        section: Optional settings section to filter by.
        fmt: Output format: ``table``, ``json``, ``env``, or ``markdown``.
        show_sensitive: If ``True``, print secret values in plain text.

    Raises:
        typer.Exit: If no rows match the requested section.
    """
    rows = _collect_rows(section, show_sensitive=show_sensitive, schema_only=False)
    if not rows:
        typer.echo(f"No settings section named {section!r}", err=True)
        raise typer.Exit(code=1)
    _dispatch(rows, fmt)


@config_app.command("schema")
def config_schema(
    section: Annotated[
        str | None,
        typer.Argument(help="Filter to a single section"),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help=_FORMAT_HELP),
    ] = "markdown",
) -> None:
    """Print the schema (defaults + descriptions) without effective values.

    Args:
        section: Optional settings section to filter by.
        fmt: Output format: ``table``, ``json``, ``env``, or ``markdown``.

    Raises:
        typer.Exit: If no rows match the requested section.
    """
    rows = _collect_rows(section, show_sensitive=False, schema_only=True)
    if not rows:
        typer.echo(f"No settings section named {section!r}", err=True)
        raise typer.Exit(code=1)
    _dispatch(rows, fmt)


def _dispatch(rows: list[dict[str, Any]], fmt: str) -> None:
    if fmt == "table":
        _print_table(rows)
    elif fmt == "json":
        _print_json(rows)
    elif fmt == "env":
        _print_env(rows)
    elif fmt == "markdown":
        _print_markdown(rows)
    else:
        typer.echo(f"Unknown format: {fmt!r}", err=True)
        raise typer.Exit(code=1)
