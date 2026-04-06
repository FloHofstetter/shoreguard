# ShoreGuard Stack — Quick Start

Run ShoreGuard + OpenShell + Paperclip with one command.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2
- ~4 GB RAM available for containers

## Setup

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
