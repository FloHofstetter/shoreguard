# Installation

## Prerequisites

- **Python 3.14** or newer
- A running [NVIDIA OpenShell](https://docs.nvidia.com/openshell/) gateway
  (or use `--local` mode for local Docker-based gateways)

## Install from PyPI

The fastest way to get started:

```bash
pip install shoreguard
```

Or, if you prefer [uv](https://docs.astral.sh/uv/):

```bash
uv pip install shoreguard
```

## Install from source

```bash
git clone https://github.com/FloHofstetter/shoreguard.git
cd shoreguard
uv sync
uv run shoreguard
```

## First run

On first launch ShoreGuard creates a SQLite database at:

```
~/.config/shoreguard/shoreguard.db
```

No manual migration step is needed — the schema is applied automatically.

Once the server is running, open your browser. The **setup wizard** appears on
the first visit and walks you through creating an admin account.

## Using PostgreSQL

SQLite works well for single-node deployments. If you need a shared database
(multiple replicas, external backups, etc.), point ShoreGuard at PostgreSQL
with the `SHOREGUARD_DATABASE_URL` environment variable:

```bash
export SHOREGUARD_DATABASE_URL="postgresql+psycopg://user:pass@db-host:5432/shoreguard"
shoreguard
```

The same variable is accepted as the `--database-url` CLI flag.
See [Configuration](../reference/configuration.md) for the full list of options.
