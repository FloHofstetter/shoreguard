# Local Mode

## What it does

Local mode enables **Docker container lifecycle management** for gateways
directly from the ShoreGuard UI. Instead of managing gateway containers
manually with `docker run`, you can create, start, stop, restart, and destroy
them from the browser.

## Enabling local mode

```bash
shoreguard --local
```

Or via environment variable:

```bash
export SHOREGUARD_LOCAL_MODE=1
shoreguard
```

## Features

When local mode is active, the gateway management page gains additional
controls:

- **Create** a new gateway container with a chosen OpenShell image.
- **Start / Stop / Restart** existing gateway containers.
- **Destroy** a gateway container and its associated data.

## Diagnostics

Local mode runs a set of preflight checks on startup and surfaces any issues
in the UI:

| Check | What it verifies |
|-------|-----------------|
| Docker daemon | Is the Docker socket reachable? |
| User permissions | Can the current user talk to Docker without `sudo`? |
| Port conflicts | Are the required ports (gRPC, HTTP) available? |
| OpenShell CLI | Is the `openshell` binary on `PATH`? |

## Auto-import

On startup, ShoreGuard scans `~/.config/openshell/gateways/` for existing
gateway configurations and imports them automatically. This means gateways
you created with the OpenShell CLI appear in the ShoreGuard dashboard without
any manual registration.

## Network restrictions

In local mode, ShoreGuard relaxes the SSRF check for private IP addresses
(specifically `127.0.0.1`) so it can communicate with gateway containers
running on the same machine.

> [!NOTE]
> Local mode is designed for development and testing. For production
> deployments, run gateways on dedicated hosts and register them as remote
> gateways instead.

## Developer workflow

For day-to-day development, combine local mode with `--no-auth` to skip login:

```bash
shoreguard --local --no-auth
```

Or equivalently:

```bash
export SHOREGUARD_LOCAL_MODE=1
export SHOREGUARD_NO_AUTH=1
shoreguard
```

This gives you:

- **SQLite** database (no PostgreSQL needed) at `~/.config/shoreguard/shoreguard.db`
- **Hot-reload** on source changes (default behaviour)
- **No login** required — all requests are treated as admin
- **Gateway lifecycle** — create/start/stop/destroy containers from the UI

### Resetting state

To start fresh, delete the SQLite database:

```bash
rm ~/.config/shoreguard/shoreguard.db
```

ShoreGuard recreates it on next startup with a fresh schema.
