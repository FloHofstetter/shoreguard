# ShoreGuard Deployment

This directory contains two **docker-compose** deployment variants for
single-VM / homelab / laptop installs. For **Kubernetes** deployments,
see [`../charts/shoreguard`](../charts/shoreguard) instead — install
that chart alongside NVIDIA's upstream OpenShell Helm chart and wire
them up via the ShoreGuard UI or API. ShoreGuard does not ship an
umbrella chart that bundles NVIDIA's OpenShell chart; that split is
intentional (see `memory/project_roadmap.md` scope boundary decision).

| File | What it runs | Use case |
|------|-------------|----------|
| `docker-compose.yml` + `Caddyfile` | Full stack: ShoreGuard + OpenShell + Paperclip + OpenClaw | Local dev / homelab / CI with real sandboxes (single-VM scale) |
| `docker-compose.standalone.yml` + `Caddyfile.standalone` | ShoreGuard + PostgreSQL + Caddy | ShoreGuard-only install connecting to a remote gateway |

## Full Stack (default)

Run ShoreGuard + OpenShell + Paperclip with one command.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2
- ~4 GB RAM available for containers

### Setup

```bash
cd deploy
cp .env.example .env

# Generate secrets
sed -i "s/change-me-to-a-random-64-char-hex-string/$(openssl rand -hex 32)/" .env
sed -i "s/change-me-to-another-random-hex-string/$(openssl rand -hex 32)/" .env

docker compose up -d
```

Wait ~60 seconds for all services to start. The `init-gateway` container will automatically register the OpenShell gateway in ShoreGuard.

## Access

| Service | URL | Description |
|---------|-----|-------------|
| ShoreGuard | http://localhost:8888 | Sandbox control plane (login: `admin@localhost` / `admin`) |
| Paperclip | http://localhost:3100 | Agent orchestration |

## Install Plugin & Adapter

Once Paperclip is bootstrapped:

1. **Install Adapter:** Settings → Adapters → Install Adapter → npm package → `paperclip-adapter-openshell-shoreguard`
2. **Install Plugin:** Settings → Plugins → Install Plugin → npm package → `paperclip-plugin-shoreguard`
3. Configure the plugin with your ShoreGuard URL (`http://shoreguard:8888`) and API key

## Create Your First Sandboxed Agent

1. Create a new agent with adapter type **Openshell Shoreguard**
2. Set the adapter config via API (see [plugin README](https://github.com/FloHofstetter/paperclip-plugin-shoreguard#quick-start))
3. Trigger a run — the agent runs inside an isolated OpenShell sandbox
4. Check ShoreGuard UI for pending network approval requests

## Teardown

```bash
docker compose down        # stop containers
docker compose down -v     # stop + delete all data
```

---

## Standalone Production

ShoreGuard + PostgreSQL + Caddy with automatic Let's Encrypt TLS. No OpenShell on the same host — connect to a remote gateway instead.

```bash
cd deploy
cp ../.env.example .env
# Edit .env: set POSTGRES_PASSWORD, SHOREGUARD_SECRET_KEY, SHOREGUARD_DOMAIN

docker compose -f docker-compose.standalone.yml up -d
```

Caddy automatically provisions TLS certificates for the domain in `SHOREGUARD_DOMAIN`. Make sure DNS points to the server and ports 80/443 are open.

---

## Monitoring

ShoreGuard exposes a Prometheus-scrapable `/metrics` endpoint. For details on the available metrics, scrape config, and example alerting rules, see [docs/integrations/prometheus.md](../docs/integrations/prometheus.md).

A starter Grafana dashboard lives at [`grafana/shoreguard.json`](grafana/shoreguard.json). Panels cover HTTP request rate, p95/p99 latency by path, gateway counts by status, operation queue depth, and webhook success rate. To import:

1. Grafana → **Dashboards** → **New** → **Import**.
2. Upload `deploy/grafana/shoreguard.json` (or paste its contents).
3. Select your Prometheus data source when prompted.

A build-info annotation overlays every `shoreguard_info` change (version, git_sha) as a vertical line, so you can correlate deploys with metric shifts.
