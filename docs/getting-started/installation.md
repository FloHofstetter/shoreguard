# Installation

## Prerequisites

- **Python 3.14** or newer
- A running [NVIDIA OpenShell](https://docs.nvidia.com/openshell/) gateway
  (or use `--local` mode for local Docker-based gateways). OpenShell
  **v0.0.26 or newer** is recommended — ShoreGuard v0.28.0 pins the
  protobuf stubs to that release and exposes features (TTY exec, gateway
  settings API, named inference routes) that require v0.0.23–v0.0.26.

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

For multi-instance deployments, ShoreGuard supports PostgreSQL. See
[Configuration — Database](../reference/configuration.md#database) for setup.

## Verifying release integrity

Starting with v0.27.0, ShoreGuard releases are signed via
[sigstore](https://sigstore.dev/) using keyless GitHub OIDC — no public
keys to distribute, every signature is logged in the public
[Rekor](https://docs.sigstore.dev/logging/overview/) transparency log.

### Docker images (GHCR)

Verify any image before running it:

```bash
cosign verify ghcr.io/flohofstetter/shoreguard:0.27.0 \
  --certificate-identity-regexp 'https://github.com/FloHofstetter/shoreguard/.*' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com'
```

Install `cosign` via Homebrew (`brew install cosign`) or from
[sigstore/cosign releases](https://github.com/sigstore/cosign/releases).

### PyPI wheels (PEP 740 attestations)

PyPI wheels are published with
[PEP 740 attestations](https://peps.python.org/pep-0740/). Modern `pip`
and `uv` verify these automatically on install. For an explicit check:

```bash
python -m pip install pypi-attestations
pypi-attestations verify-pypi \
  --repository https://pypi.org/simple/ \
  pypi:shoreguard==0.27.0
```
