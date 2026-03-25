# Shoreguard

[![CI](https://github.com/FloHofstetter/shoreguard/actions/workflows/ci.yml/badge.svg)](https://github.com/FloHofstetter/shoreguard/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

Open source control plane for [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell). A web-based GUI to manage AI agent sandboxes, security policies, and approval flows.

> [!WARNING]
> **Weekend project.** This UI was vibe-coded in a weekend as a proof of concept. It works, it has tests, but it is not production-hardened. There is no authentication, no rate limiting, and no audit logging. Use it for local development and demos — not to secure anything that matters.

![Sandbox Overview](docs/screenshots/sandbox-overview.png)

## What is this?

OpenShell provides secure, sandboxed environments for AI agents (OpenClaw, Claude Code, Cursor, etc.). Shoreguard gives you a dashboard to:

- **Manage sandboxes** — Create, monitor, and delete agent sandboxes
- **Edit security policies** — Visual network policy editor instead of raw YAML
- **Approve access requests** — iOS-style permission dialogs when agents try to reach blocked endpoints
- **Live monitoring** — Real-time logs and events via WebSocket
- **One-click setup** — Wizard to create sandboxes with pre-configured policy presets

## Quick Start

**Prerequisites:** Python 3.12+, a running [OpenShell](https://github.com/NVIDIA/OpenShell) gateway

### Install from PyPI

```bash
pip install shoreguard
shoreguard
```

### Install from source

```bash
git clone https://github.com/FloHofstetter/shoreguard.git
cd shoreguard
uv sync
uv run shoreguard
```

Open [http://localhost:8888](http://localhost:8888) in your browser.

> Shoreguard auto-discovers your OpenShell gateway via `~/.config/openshell/active_gateway`. If no gateway is configured, the UI loads but API calls will fail.

### CLI Options

```
shoreguard --help
shoreguard --port 9000 --host 127.0.0.1
shoreguard --log-level debug --no-reload
```

| Flag | Env Variable | Default | Description |
|------|-------------|---------|-------------|
| `--host` | `SHOREGUARD_HOST` | `0.0.0.0` | Bind address |
| `--port` | `SHOREGUARD_PORT` | `8888` | Bind port |
| `--log-level` | `SHOREGUARD_LOG_LEVEL` | `info` | Log level (debug/info/warning/error) |
| `--no-reload` | `SHOREGUARD_RELOAD` | reload on | Disable auto-reload |

CLI arguments take priority over environment variables.

## Features

### Gateway Management

Multi-gateway support with status monitoring, start/stop controls, and system diagnostics.

![Gateways](docs/screenshots/gateways.png)

### Policy Management

Visual network policy editor with per-rule endpoint details and binary restrictions.

![Network Policies](docs/screenshots/network-policies.png)

- View and edit network policies per sandbox
- Apply bundled presets with one click (PyPI, npm, Docker Hub, Slack, Discord, etc.)
- Policy revision history with rollback capability

### Approval Flow

When an agent in a sandbox tries to access a blocked endpoint, OpenShell generates a draft policy recommendation. Shoreguard surfaces these as approval requests:

- Review proposed network rules with rationale and security notes
- Approve, reject, or edit individual rules
- Bulk approve/reject with security-flagged chunk protection
- Undo approved rules
- Real-time WebSocket notifications for new approval requests

### Sandbox Wizard

Step-by-step sandbox creation with agent type selection, configuration, policy presets, and live launch progress.

![Wizard](docs/screenshots/wizard.png)

### Bundled Policy Presets

| Preset | Description |
|--------|-------------|
| `pypi` | Python Package Index (pypi.org) |
| `npm` | npm + Yarn registries |
| `docker` | Docker Hub + NVIDIA Container Registry |
| `huggingface` | HF Hub, LFS, and Inference API |
| `slack` | Slack API and webhooks |
| `discord` | Discord API, gateway, and CDN |
| `telegram` | Telegram Bot API |
| `jira` | Jira / Atlassian Cloud |
| `outlook` | Microsoft Graph / Outlook |

## Architecture

```
┌─────────────────────────────────────────────┐
│  Browser (:8888)                            │
│  ├── Dashboard        (Bootstrap 5 + JS)    │
│  ├── Policy Editor                          │
│  ├── Approval Flow                          │
│  └── Sandbox Wizard                         │
├─────────────────────────────────────────────┤
│  Shoreguard API       (FastAPI)             │
│  ├── REST endpoints   /api/*                │
│  ├── WebSocket        /ws/{sandbox}         │
│  └── Static files     /static/*             │
├─────────────────────────────────────────────┤
│  Service Layer        (Business Logic)      │
│  ├── GatewayService   Docker, ports, health │
│  ├── SandboxService   Create + presets      │
│  ├── PolicyService    Rule CRUD, merge      │
│  └── ProviderService  Types, credentials    │
├─────────────────────────────────────────────┤
│  Client Layer         (gRPC + mTLS)         │
│  ├── SandboxManager   CRUD, exec, logs      │
│  ├── PolicyManager    policies, presets      │
│  └── ApprovalManager  draft policy flow     │
├─────────────────────────────────────────────┤
│  OpenShell Gateway    (gRPC)                │
│  └── Docker / Kubernetes Sandboxes          │
└─────────────────────────────────────────────┘
```

## API

Shoreguard exposes a REST API on port 8888. Interactive docs are available at [/docs](http://localhost:8888/docs) (Swagger UI).

### Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sandboxes` | List all sandboxes |
| `POST` | `/api/sandboxes` | Create a sandbox |
| `GET` | `/api/sandboxes/{name}` | Get sandbox details |
| `DELETE` | `/api/sandboxes/{name}` | Delete a sandbox |
| `POST` | `/api/sandboxes/{name}/exec` | Execute a command |
| `GET` | `/api/sandboxes/{name}/policy` | Get active policy |
| `PUT` | `/api/sandboxes/{name}/policy` | Update policy |
| `GET` | `/api/sandboxes/{name}/approvals/pending` | Get pending approvals |
| `POST` | `/api/sandboxes/{name}/approvals/{id}/approve` | Approve a request |
| `POST` | `/api/sandboxes/{name}/approvals/{id}/reject` | Reject a request |
| `GET` | `/api/policies/presets` | List available presets |
| `WS` | `/ws/{name}` | Live sandbox events |

## Development

```bash
# Install with dev dependencies
uv sync --group dev

# Run the server with auto-reload
uv run shoreguard

# Lint and format
uv run ruff check .
uv run ruff format --check .

# Type checking
uv run pyright

# Unit tests
uv run pytest -m 'not integration'

# Integration tests (requires running OpenShell gateway)
uv run pytest tests/integration/ -m integration

# All tests
uv run pytest

# Mutation testing
uv run mutmut run
```

### Test suite

| Category | Tests | Description |
|----------|-------|-------------|
| Unit | 425 | Client managers, services, API routes, converters, CLI |
| Integration | 35 | Live gRPC against real OpenShell gateway |
| Mutation | 72% kill rate | Via mutmut, measures test quality |

### OpenShell metadata (`openshell.yaml`)

Shoreguard needs metadata about OpenShell that is not available via the gRPC API: provider types with their credential environment variables, inference provider profiles, and community sandbox templates.

This metadata lives in [`shoreguard/openshell.yaml`](shoreguard/openshell.yaml). When OpenShell updates its provider registry or community sandbox list, update this file to match. The sync sources are documented at the top of the file:

| Data | OpenShell source |
|------|-----------------|
| Provider types | `crates/openshell-providers/src/lib.rs` (`ProviderRegistry::new`) |
| Credential keys | `crates/openshell-providers/src/<type>.rs` (discovery logic) |
| Inference providers | `crates/openshell-core/src/inference.rs` (`profile_for`) |
| Community sandboxes | `docs/sandboxes/community-sandboxes.md` |

### Regenerating proto stubs

If the OpenShell proto files change:

```bash
uv run python scripts/generate_proto.py /path/to/OpenShell/proto
```

## Roadmap

- [ ] Multi-cluster support
- [ ] Policy diff viewer
- [ ] Audit log export
- [ ] User authentication

## Contributing

1. Open an issue to discuss changes before submitting a PR
2. Run the full check suite before pushing:

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -m 'not integration'
```

3. All CI checks must pass (lint, typecheck, tests on Python 3.12 + 3.13)

## License

[Apache 2.0](LICENSE)
