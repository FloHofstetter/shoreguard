# Installation

## Prerequisites

- **Python 3.14** or newer
- A running [NVIDIA OpenShell](https://docs.nvidia.com/openshell/) gateway
  (or use `--local` mode for local Docker-based gateways). After the M38
  upstream-sync ShoreGuard requires OpenShell **`v0.0.57` or newer**: the
  generated stubs expect the v0.0.57 wire shapes, and in particular
  [PR #1565](https://github.com/NVIDIA/OpenShell/pull/1565) moved
  `Sandbox.phase` / `current_policy_version` into the `SandboxStatus`
  sub-message, so an older gateway reports an empty phase and stalls
  sandbox-ready waits. There is no compat shim. Individual features still
  trace back to the release that first introduced them; see the matrix.

!!! info "Compatibility matrix"
    | Feature | Minimum gateway version |
    | --- | --- |
    | `Sandbox.status.phase` / `current_policy_version` move | `v0.0.57` (PR #1565) |
    | Provider credential refresh / rotation (`*ProviderRefresh`, `RotateProviderCredential`) | `v0.0.57` (PR #1349) |
    | Local-domain service routing (`ExposeService` / `*Service`) | `v0.0.57` (PR #1101) |
    | Interactive exec (`ExecSandboxInteractive`) | `v0.0.57` (PR #1331) |
    | TCP / SSH forward (`ForwardTcp`) | `v0.0.57` (PR #1029) |
    | Gateway sandbox tokens (`IssueSandboxToken` / `RefreshSandboxToken`) | `v0.0.57` (PR #1404) |
    | Core wire surface (post-`ObjectMeta`) | `v0.0.37` |
    | Sandbox-provider attach lifecycle (`*SandboxProvider`) | `v0.0.37` (PR #1242) |
    | Provider-profile registry (`*ProviderProfile`) | `v0.0.37` (PR #1170) |
    | GraphQL L7 inspection (`protocol=graphql`, `operation_*`, `fields`) | `v0.0.37` (PR #1083) |
    | L7 deny rules, TLD rejection, SSE hardening | `v0.0.30` |
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

!!! info "arm64 / DGX Spark"
    ShoreGuard is a first-class citizen on aarch64 — DGX Spark and the
    other NVIDIA GB10 boxes, Raspberry Pi, ARM servers. The release
    container images are multi-arch (`linux/amd64` + `linux/arm64`) and CI
    runs the unit suite natively on arm64. See
    [Homelab / Single Box](../operations/homelab.md) for the
    one-container SQLite install and the systemd unit.

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

### Headless first-admin (no browser)

On an SSH-only box you can create the first admin without the browser wizard —
either seed it from an env var on first start, or create it from the CLI:

```bash
# Seeds admin@localhost on first start, only while no users exist
SHOREGUARD_ADMIN_PASSWORD='choose-a-strong-one' shoreguard

# …or create any user explicitly
shoreguard create-user you@example.com --role admin
```

Single-box developers can skip auth entirely with `--local --no-auth`; see the
[Solo Dev guide](solo-dev.md).

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
