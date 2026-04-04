# Contributing

Thank you for your interest in contributing to ShoreGuard! This guide covers the
workflow, setup, and standards we follow.

## Issue-first workflow

Before submitting a pull request, **open an issue** to discuss the change you have
in mind. This helps avoid duplicated effort and ensures alignment on scope and
approach. Once the issue is triaged, you can reference it in your PR.

## Setup

```bash
git clone https://github.com/FloHofstetter/shoreguard.git
cd shoreguard
uv sync --group dev
```

This installs all runtime and development dependencies in an isolated virtual
environment managed by [uv](https://docs.astral.sh/uv/).

## Running the server

```bash
uv run shoreguard --local --no-auth
```

The server starts on `http://localhost:8888` by default. The `--local` flag
enables Docker-based gateway management, `--no-auth` skips login.

## Clone to first sandbox

This walkthrough takes you from a fresh clone to creating your first sandbox.

### Prerequisites

- Python 3.14+, [uv](https://docs.astral.sh/uv/)
- Docker Engine running (`docker info` should succeed)
- [openshell](https://github.com/NVIDIA/OpenShell) CLI on PATH

### Steps

**1. Clone and install:**

```bash
git clone https://github.com/FloHofstetter/shoreguard.git
cd shoreguard
uv sync --group dev
```

**2. Start ShoreGuard in local mode:**

```bash
uv run shoreguard --local --no-auth
```

This starts the server on `http://localhost:8888` with SQLite, no login
required, and Docker gateway management enabled.

**3. Create a gateway:**

Open [http://localhost:8888](http://localhost:8888). Click **Gateways** >
**Create Gateway**. Pick a name (e.g. `dev`) and click Create. ShoreGuard
calls `openshell gateway start` under the hood.

**4. Create a sandbox:**

Navigate to the gateway page, click **Create Sandbox**. The wizard walks
you through selecting an image and configuring policies.

**5. Verify:**

You should see the sandbox listed on the gateway detail page with status
"running".

## Task runner

ShoreGuard uses [just](https://github.com/casey/just) for common dev tasks:

```bash
just dev       # start dev server (--local --no-auth)
just check     # run full check suite
just test      # run unit tests
just format    # auto-format code
just docs      # serve docs locally
just sync      # sync dependencies
```

Run `just --list` to see all available tasks.

## Check suite

Run the full check suite **before pushing** to make sure CI will pass:

```bash
just check
```

Or manually:

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -m 'not integration'
```

This covers linting, formatting, type checking, and unit tests in a single
command.

## CI requirements

All of the following checks must pass on every pull request:

- **Lint** -- ruff check
- **Format** -- ruff format
- **Typecheck** -- pyright
- **Tests** -- pytest on Python 3.14

## Code style

| Area         | Standard                                        |
| ------------ | ----------------------------------------------- |
| Docstrings   | Google-style                                    |
| Linting      | [ruff](https://docs.astral.sh/ruff/)            |
| Formatting   | ruff format                                     |
| Type checking| [pyright](https://github.com/microsoft/pyright) |
| Line length  | **100 characters**                              |
