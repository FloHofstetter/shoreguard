# ShoreGuard

[![CI](https://github.com/FloHofstetter/shoreguard/actions/workflows/ci.yml/badge.svg)](https://github.com/FloHofstetter/shoreguard/actions/workflows/ci.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

**Open-source control plane for [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell).** Manage AI agent sandboxes, inference routing, and security policies — from a web UI, REST API, or Terraform.

![Sandbox Overview](docs/screenshots/sandbox-overview.png)

---

## Architecture

ShoreGuard sits between operators and OpenShell's secure runtime. Agents run inside hardened sandboxes with routed inference — they never see real API keys or provider endpoints.

```mermaid
graph LR
    subgraph "Operators — all use ShoreGuard REST API"
        UI["ShoreGuard Web UI"]
        TF["Terraform Provider"]
        PC["Paperclip"]
        OC["OpenClaw"]
    end

    subgraph "Observability"
        Grafana["Grafana"]
    end

    subgraph "ShoreGuard — Management Plane"
        SG["ShoreGuard API"]
        DB[("PostgreSQL")]
        Metrics["/metrics"]
    end

    subgraph "Gateway: dev"
        OS1["OpenShell Controller"]
        subgraph "Sandbox"
            Agent1["Agent"]
        end
        Proxy1["inference.local/v1"]
    end

    subgraph "Gateway: staging"
        OS2["OpenShell Controller"]
        subgraph "Sandbox "
            Agent2["Agent"]
        end
        Proxy2["inference.local/v1"]
    end

    subgraph "LLM Providers"
        LLM["Anthropic / NVIDIA / OpenAI"]
    end

    UI --> SG
    TF --> SG
    PC -->|"adapter plugin"| SG
    OC -->|"slash commands"| SG
    PC -.->|"controls"| Agent1
    OC -.->|"controls"| Agent1
    Grafana --> Metrics
    SG --> DB
    SG --> Metrics
    SG -- "gRPC + mTLS" --> OS1
    SG -- "gRPC + mTLS" --> OS2
    OS1 --> Agent1
    OS2 --> Agent2
    Agent1 -. "inference.local" .-> Proxy1
    Agent2 -. "inference.local" .-> Proxy2
    Proxy1 -- "real API key" --> LLM
    Proxy2 -- "real API key" --> LLM

    style SG fill:#1a7f37,color:#fff,stroke:#1a7f37
    style Agent1 fill:#c8e6c9,stroke:#388e3c,color:#1b5e20
    style Agent2 fill:#c8e6c9,stroke:#388e3c,color:#1b5e20
    style Proxy1 fill:#ffe0b2,stroke:#e65100,color:#bf360c
    style Proxy2 fill:#ffe0b2,stroke:#e65100,color:#bf360c
    style Grafana fill:#bbdefb,stroke:#1565c0,color:#0d47a1
```

> **Key insight:** The agent inside the sandbox only knows `inference.local/v1`. OpenShell's L7 proxy injects the real credentials and routes to the actual provider. API keys are managed by ShoreGuard, never exposed to agent code. All operators — whether human (Web UI, Terraform) or agent platforms (Paperclip, OpenClaw) — use the same ShoreGuard REST API. One ShoreGuard instance manages multiple gateways (dev, staging, production).

---

## Why ShoreGuard?

[NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) provides hardened sandboxes for AI agents — but ships with only a CLI. [NemoClaw](https://github.com/NVIDIA/NemoClaw) adds orchestration, but is single-gateway and CLI-driven.

ShoreGuard adds the missing management layer:

| Capability | OpenShell CLI | NemoClaw | ShoreGuard |
|------------|:---:|:---:|:---:|
| Sandbox creation | CLI | CLI | Web UI + API + Terraform |
| Multi-gateway | — | — | Multiple gateways, one dashboard |
| Visual policy editor | — | — | Drag-and-drop with revision history |
| Approval flow | — | — | Real-time notifications |
| Inference routing | CLI | Blueprint profiles | API-driven, per-gateway |
| Audit trail | — | — | Persistent, filterable, exportable |
| RBAC | — | — | Admin / Operator / Viewer |
| Agent frameworks | — | OpenClaw only | Paperclip, OpenClaw, custom |
| Webhooks | — | — | Slack, Discord, Email, HMAC-signed |

---

## Quick Start

### Local development

```bash
pip install shoreguard
shoreguard --local --no-auth
```

Open [http://localhost:8888](http://localhost:8888). The `--local` flag enables Docker-based gateway management, `--no-auth` skips login.

### Docker Compose (production)

```bash
git clone https://github.com/FloHofstetter/shoreguard.git
cd shoreguard/deploy
cp .env.example .env    # edit: set SHOREGUARD_SECRET_KEY, passwords
docker compose up -d    # core: ShoreGuard + OpenShell + Caddy (HTTPS)
```

The stack automatically generates mTLS certificates, registers an OpenShell gateway, and provides HTTPS via Caddy with self-signed certificates.

#### Optional profiles

```bash
# Add Paperclip agent orchestration
docker compose --profile paperclip up -d

# Add OpenClaw agent gateway (sandboxed)
docker compose --profile openclaw up -d
```

See the [deployment guide](https://flohofstetter.github.io/shoreguard/admin/deployment/) for production hardening, custom domains, and Let's Encrypt.

### Verifying release integrity

Docker images on GHCR and wheels on PyPI are signed via [sigstore](https://sigstore.dev/) keyless (GitHub OIDC → Fulcio → Rekor). Verify before running:

```bash
cosign verify ghcr.io/flohofstetter/shoreguard:0.27.0 \
  --certificate-identity-regexp 'https://github.com/FloHofstetter/shoreguard/.*' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com'
```

PyPI wheels ship with [PEP 740 attestations](https://peps.python.org/pep-0740/) — modern `pip`/`uv` verify them automatically. See the [installation guide](https://flohofstetter.github.io/shoreguard/getting-started/installation/#verifying-release-integrity) for explicit verification.

---

## Features

### Sandbox Management

- **Sandbox wizard** — step-by-step creation with community images, GPU support, and policy presets
- **Visual policy editor** — network rules, filesystem paths, process settings with revision history and diff viewer
- **Approval flow** — agents request endpoint access, operators approve or deny in real-time
- **Templates** — pre-configured sandboxes for data science, web development, and secure coding

### Infrastructure

- **Multi-gateway** — manage dev, staging, and production OpenShell clusters from one dashboard
- **RBAC** — Admin, Operator, Viewer roles with gateway-scoped overrides
- **Audit log** — persistent, filterable, exportable trail of all state changes
- **Health monitoring** — automatic gateway probing with status indicators

### Integrations

- **REST API** — full CRUD for gateways, sandboxes, policies, providers, and inference
- **Terraform provider** — declarative infrastructure as code
- **Webhooks** — Slack, Discord, Email, and generic webhooks with HMAC-SHA256 signing
- **Prometheus metrics** — `/metrics` endpoint for Grafana and standard monitoring

<details>
<summary><strong>Screenshots</strong></summary>

| Sandbox Overview | Policy Editor |
|:---:|:---:|
| ![Sandbox Overview](docs/screenshots/sandbox-overview.png) | ![Policy Editor](docs/screenshots/policy.png) |

| Network Policies | Gateway Detail |
|:---:|:---:|
| ![Network Policies](docs/screenshots/network-policies.png) | ![Gateway Detail](docs/screenshots/gateway-detail.png) |

| Providers | Audit Log |
|:---:|:---:|
| ![Providers](docs/screenshots/providers.png) | ![Audit Log](docs/screenshots/audit-log.png) |

</details>

---

## Ecosystem

| Project | Description |
|---------|-------------|
| [Terraform Provider](https://github.com/FloHofstetter/terraform-provider-shoreguard) | Manage gateways, sandboxes, providers, and policies as code |
| [Paperclip Plugin + Adapter](https://github.com/FloHofstetter/paperclip-plugin-shoreguard) | Run Paperclip agents in isolated OpenShell sandboxes |
| [OpenClaw Plugin](https://github.com/FloHofstetter/openclaw-plugin-shoreguard) | `/shoreguard` slash commands for OpenClaw agents |
| [OpenClaw Sandbox Image](images/openclaw/) | Hardened OpenClaw image for OpenShell deployment |
| [Docker Compose Stack](deploy/) | One-command setup: ShoreGuard + OpenShell + Caddy + optional integrations |

---

## Roadmap

**Shipped:**

- Multi-gateway management with health monitoring
- RBAC with gateway-scoped overrides
- Sandbox wizard with community images and presets
- Visual policy editor with revision history
- Real-time approval flow
- Terraform provider
- Persistent audit log with export
- Webhooks (Slack, Discord, Email) with HMAC signing
- Prometheus metrics
- Paperclip adapter ([`@shoreguard/paperclip-plugin`](https://www.npmjs.com/package/@shoreguard/paperclip-plugin) + [`@shoreguard/paperclip-adapter`](https://www.npmjs.com/package/@shoreguard/paperclip-adapter))
- Docker Compose stack with Caddy auto-TLS
- Inference routing via OpenShell L7 proxy
- OpenClaw sandbox image with NemoClaw-style hardening

**In progress:**

- Hardened sandbox deployment via gRPC API (blocked by [OpenShell API limitations](images/openclaw/README.md#known-limitations))
- Routed inference for Paperclip adapter (replace credential injection with `inference.local`)

**Planned:**

- Multi-region gateway federation
- DigitalOcean Marketplace integration

---

## Development

```bash
git clone https://github.com/FloHofstetter/shoreguard.git
cd shoreguard
uv sync --group dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
uv run shoreguard --local --no-auth
```

Run checks with [just](https://github.com/casey/just):

```bash
just check    # lint + format + typecheck + tests
just dev      # start dev server
just test     # run unit tests
```

See the [contributing guide](https://flohofstetter.github.io/shoreguard/development/contributing/) for details.

## Documentation

Full docs: **[flohofstetter.github.io/shoreguard](https://flohofstetter.github.io/shoreguard/)**

## License

[Apache 2.0](LICENSE)
