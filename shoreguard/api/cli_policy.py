"""``shoreguard policy`` CLI subcommands (M23 GitOps).

Three commands wrap the M23 REST endpoints so CI pipelines can drive
policy changes from a Git repo:

- ``shoreguard policy export``  — fetch deterministic YAML for a sandbox
- ``shoreguard policy diff``    — dry-run apply, show structured drift
- ``shoreguard policy apply``   — write the policy (or vote under M19)

Exit codes:
- ``0`` up-to-date / successful apply
- ``1`` drift detected / vote recorded — CI step should fail loudly
- ``2`` operational error (network, 4xx, 5xx, parse error)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

policy_app = typer.Typer(
    name="policy",
    help="GitOps policy operations (export / diff / apply).",
    no_args_is_help=True,
)


def _resolve_url(url: str | None) -> str:
    return (url or os.environ.get("SHOREGUARD_URL", "http://localhost:8888")).rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("SHOREGUARD_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _read_yaml(file: Path) -> str:
    if str(file) == "-":
        return sys.stdin.read()
    try:
        return file.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"error: cannot read {file}: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _format_diff(diff: dict[str, Any]) -> str:
    lines: list[str] = []
    fs = diff.get("filesystem") or {}
    for key in ("read_only_added", "read_only_removed", "read_write_added", "read_write_removed"):
        for v in fs.get(key, []):
            sign = "+" if "added" in key else "-"
            lines.append(f"  {sign} filesystem.{key.split('_added')[0].split('_removed')[0]}: {v}")
    if "include_workdir_changed" in fs:
        old, new = fs["include_workdir_changed"]
        lines.append(f"  ~ filesystem.include_workdir: {old} → {new}")
    proc = diff.get("process") or {}
    for k, (old, new) in proc.items():
        lines.append(f"  ~ process.{k.removesuffix('_changed')}: {old!r} → {new!r}")
    np = diff.get("network_policies") or {}
    for v in np.get("added", []):
        lines.append(f"  + network_policies.{v}")
    for v in np.get("removed", []):
        lines.append(f"  - network_policies.{v}")
    for v in np.get("changed", []):
        lines.append(f"  ~ network_policies.{v}")
    return "\n".join(lines) if lines else "  (no changes)"


def _is_drift(diff: dict[str, Any]) -> bool:
    if diff.get("filesystem"):
        return True
    if diff.get("process"):
        return True
    np = diff.get("network_policies") or {}
    return bool(np.get("added") or np.get("removed") or np.get("changed"))


def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        with httpx.Client(timeout=30.0, headers=_headers()) as client:
            return client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        typer.echo(f"error: HTTP request failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@policy_app.command("export")
def export_cmd(
    gateway: Annotated[str, typer.Option("--gateway", "-g", help="Gateway name")],
    sandbox: Annotated[str, typer.Option("--sandbox", "-s", help="Sandbox name")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output file (default: stdout)"),
    ] = None,
    url: Annotated[str | None, typer.Option("--url", help="ShoreGuard base URL")] = None,
) -> None:
    """Export the active sandbox policy as deterministic YAML.

    Args:
        gateway: Gateway name.
        sandbox: Sandbox name.
        output: Output file (default: stdout).
        url: ShoreGuard base URL override.

    Raises:
        typer.Exit: Exit code 2 on HTTP error.
    """
    base = _resolve_url(url)
    resp = _request("GET", f"{base}/api/gateways/{gateway}/sandboxes/{sandbox}/policy/export")
    if resp.status_code != 200:
        typer.echo(f"error: export failed ({resp.status_code}): {resp.text}", err=True)
        raise typer.Exit(code=2)
    yaml_text = resp.json()["yaml"]
    if output is None or str(output) == "-":
        typer.echo(yaml_text, nl=False)
    else:
        output.write_text(yaml_text, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)


@policy_app.command("diff")
def diff_cmd(
    gateway: Annotated[str, typer.Option("--gateway", "-g")],
    sandbox: Annotated[str, typer.Option("--sandbox", "-s")],
    file: Annotated[Path, typer.Option("--file", "-f", help="YAML file (or '-' for stdin)")],
    url: Annotated[str | None, typer.Option("--url")] = None,
) -> None:
    """Dry-run a policy apply and show the structured diff. Exits 1 on drift.

    Args:
        gateway: Gateway name.
        sandbox: Sandbox name.
        file: YAML file path (or ``-`` for stdin).
        url: ShoreGuard base URL override.

    Raises:
        typer.Exit: Exit code 1 on drift, 2 on HTTP/other error.
    """
    base = _resolve_url(url)
    yaml_text = _read_yaml(file)
    resp = _request(
        "POST",
        f"{base}/api/gateways/{gateway}/sandboxes/{sandbox}/policy/apply",
        json={"yaml": yaml_text, "dry_run": True},
    )
    if resp.status_code == 423:
        typer.echo(f"error: policy is pinned: {resp.text}", err=True)
        raise typer.Exit(code=2)
    if resp.status_code == 409:
        typer.echo(f"error: version mismatch: {resp.text}", err=True)
        raise typer.Exit(code=2)
    if resp.status_code != 200:
        typer.echo(f"error: diff failed ({resp.status_code}): {resp.text}", err=True)
        raise typer.Exit(code=2)
    body = resp.json()
    diff = body.get("diff") or {}
    typer.echo(_format_diff(diff))
    if _is_drift(diff):
        raise typer.Exit(code=1)


@policy_app.command("apply")
def apply_cmd(
    gateway: Annotated[str, typer.Option("--gateway", "-g")],
    sandbox: Annotated[str, typer.Option("--sandbox", "-s")],
    file: Annotated[Path, typer.Option("--file", "-f", help="YAML file (or '-' for stdin)")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Plan without writing")] = False,
    expected_version: Annotated[
        str | None,
        typer.Option("--expected-version", help="Optimistic-lock etag override"),
    ] = None,
    url: Annotated[str | None, typer.Option("--url")] = None,
) -> None:
    """Apply a YAML policy. Exits 1 on drift-but-not-applied or vote recorded.

    Args:
        gateway: Gateway name.
        sandbox: Sandbox name.
        file: YAML file path (or ``-`` for stdin).
        dry_run: When true, plan without writing.
        expected_version: Optional optimistic-lock etag override.
        url: ShoreGuard base URL override.

    Raises:
        typer.Exit: Exit code 1 on drift / vote recorded, 2 on HTTP error.
    """
    base = _resolve_url(url)
    yaml_text = _read_yaml(file)
    payload: dict[str, Any] = {"yaml": yaml_text, "dry_run": dry_run}
    if expected_version:
        payload["expected_version"] = expected_version
    resp = _request(
        "POST",
        f"{base}/api/gateways/{gateway}/sandboxes/{sandbox}/policy/apply",
        json=payload,
    )
    if resp.status_code == 423:
        typer.echo(f"error: policy is pinned: {resp.text}", err=True)
        raise typer.Exit(code=2)
    if resp.status_code == 409:
        typer.echo(f"error: version mismatch: {resp.text}", err=True)
        raise typer.Exit(code=2)
    if resp.status_code == 202:
        body = resp.json()
        typer.echo(
            f"vote recorded: {body.get('votes_cast')}/{body.get('votes_needed')} approvals",
            err=True,
        )
        typer.echo(_format_diff(body.get("diff") or {}))
        raise typer.Exit(code=1)
    if resp.status_code != 200:
        typer.echo(f"error: apply failed ({resp.status_code}): {resp.text}", err=True)
        raise typer.Exit(code=2)
    body = resp.json()
    status = body.get("status")
    diff = body.get("diff") or {}
    if status == "up_to_date":
        typer.echo("up-to-date")
        return
    if status == "dry_run":
        typer.echo(_format_diff(diff))
        if _is_drift(diff):
            raise typer.Exit(code=1)
        return
    if status == "applied":
        typer.echo(f"applied (version {body.get('applied_version')})")
        typer.echo(_format_diff(diff))
        return
    typer.echo(f"unknown status: {status}", err=True)
    raise typer.Exit(code=2)
