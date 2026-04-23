# Installation

## Prerequisites

- **Python 3.14** or newer
- A running [NVIDIA OpenShell](https://docs.nvidia.com/openshell/) gateway
  (or use `--local` mode for local Docker-based gateways). OpenShell
  **v0.0.36 or newer** is recommended. ShoreGuard's protobuf stubs are
  byte-parity with upstream `proto/` at `v0.0.36`, and the baseline wire
  surface ShoreGuard consumes has not changed since `v0.0.30`, so any
  gateway `≥ v0.0.30` is wire-compatible for existing flows. Individual
  features still trace back to the release that first introduced them
  (TTY exec from v0.0.23, settings API and named inference routes from
  v0.0.25–v0.0.26, L7 path canonicalization from v0.0.34, incremental
  policy merge operations from v0.0.33 — see the compatibility matrix
  below).

!!! info "Compatibility matrix"
    | Feature | Minimum gateway version |
    | --- | --- |
    | Core wire surface | `v0.0.30` |
    | L7 deny rules, TLD rejection, SSE hardening (M29) | `v0.0.30` |
    | `NetworkEndpoint.allow_encoded_slash` (GitLab-style `%2F` paths) | `v0.0.30` |
    | L7 path canonicalization parity | `v0.0.34` |
    | `SSH session response` charset contract | `v0.0.34` |
    | `/policy/apply?mode=merge` (incremental policy updates) | `v0.0.33` |

!!! tip "Gateway-only install (OpenShell ≥ v0.0.32)"
    Upstream now publishes a standalone `openshell-gateway` binary per
    release alongside the full cluster image. ShoreGuard treats this
    binary like any other gateway — point it at an LLM provider, start
    it listening on port `30051`, and register it via
    `openshell gateway register` or ShoreGuard's
    `POST /gateway/register`. Useful when you want a single gateway
    process on a VM without the k3s-in-container footprint.

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
