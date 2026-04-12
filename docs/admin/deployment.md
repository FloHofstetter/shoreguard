# Deployment

!!! tip "Kubernetes?"
    For production Kubernetes deployments, see the
    [Production Kubernetes runbook](../deploy/production-k8s.md).

## Development mode

By default, ShoreGuard starts with auto-reload enabled — the server restarts
automatically when source files change:

```bash
shoreguard
```

This is convenient for development but should not be used in production.

> [!TIP]
> Always use `--no-reload` in production to avoid unnecessary restarts and
> file-watching overhead.

## Docker

ShoreGuard ships with a `Dockerfile` and `docker-compose.yml` for production
deployments with PostgreSQL.

### Prerequisites

- Docker Engine 24+ with Compose v2
- At least 1 GB RAM available for the stack
- A domain name and TLS certificate (for production)

### Step-by-step setup

**1. Clone the repository:**

```bash
git clone https://github.com/FloHofstetter/shoreguard.git
cd shoreguard
```

**2. Create an environment file:**

```bash
cp .env.example .env
```

**3. Set secrets** — edit `.env` and replace the placeholder values:

```bash
POSTGRES_PASSWORD=<strong-random-password>
SHOREGUARD_SECRET_KEY=<strong-random-secret>
```

Generate secure values with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Optionally set `SHOREGUARD_ADMIN_PASSWORD` to bootstrap the first admin account
without the setup wizard.

**4. Start the stack:**

```bash
docker compose up -d
```

This starts two containers: ShoreGuard (port 8888) and PostgreSQL 17.

**5. Verify:**

```bash
curl -s http://localhost:8888/healthz
# {"status":"ok"}
```

Open [http://localhost:8888](http://localhost:8888) and complete the setup
wizard (or skip it if `SHOREGUARD_ADMIN_PASSWORD` was set).

### Environment variables

The most important variables for Docker deployments:

- `POSTGRES_PASSWORD` — PostgreSQL password (**required**)
- `SHOREGUARD_SECRET_KEY` — HMAC secret for session cookies (**required**)
- `SHOREGUARD_ADMIN_PASSWORD` — bootstrap admin account (skip wizard)

See the [Configuration reference](../reference/configuration.md) for the
complete list of all environment variables.

### Health probes

Health probes for container orchestration:

- `GET /healthz` — liveness (process running)
- `GET /readyz` — readiness (database reachable, services initialised)

### Monitoring

ShoreGuard exposes a Prometheus-compatible `/metrics` endpoint. See the
[Prometheus integration guide](../integrations/prometheus.md) for scrape
configuration and the full metric list.

### Volumes and backups

The `pgdata` volume persists PostgreSQL data across container restarts.

**Backup:**

```bash
docker compose exec db pg_dump -U shoreguard shoreguard > backup.sql
```

**Restore:**

```bash
docker compose exec -T db psql -U shoreguard shoreguard < backup.sql
```

> [!TIP]
> Schedule regular backups with cron. For point-in-time recovery, consider
> PostgreSQL WAL archiving.

### Network isolation

The `docker-compose.yml` defines a dedicated `shoreguard-net` bridge network.
PostgreSQL is only reachable from within this network — it does not expose a
port to the host. Only the ShoreGuard HTTP port is published.

### Development compose

For local development with Docker, use the standalone dev compose file:

```bash
docker compose -f docker-compose.dev.yml up --build
```

This runs ShoreGuard with SQLite, hot-reload, no authentication, and local
gateway management enabled. No PostgreSQL required.

## Upgrading

**1. Back up the database** before every upgrade. The bundled backup script
auto-detects SQLite vs Postgres:

```bash
uv run python -m scripts.backup --target /var/backups/shoreguard
```

See [Database Migrations — Before Any Migration](database-migrations.md#before-any-migration)
for details and the low-level equivalents.

**2. Pull the latest changes and rebuild:**

```bash
git pull
docker compose build
docker compose up -d
```

**3. Database migrations** are applied automatically on startup. No manual
migration step is needed.

**4. Verify** with the readiness and version probes:

```bash
curl -s http://localhost:8888/readyz | python3 -m json.tool
curl -s http://localhost:8888/version
```

The `/version` endpoint reports the version, git SHA, and build time of the
running binary — use it to confirm the new image actually landed.

**5. If something breaks:** follow the [Rollback Runbook](rollback.md). Check
the [changelog](../changelog.md) for breaking changes before redeploying.

## Production flags

```bash
shoreguard --no-reload --host 127.0.0.1
```

Binding to `127.0.0.1` ensures ShoreGuard only accepts connections from
localhost. Use a reverse proxy to expose it to the network.

## Database

SQLite is used by default (no setup required). For multi-instance or production
deployments, use PostgreSQL. See
[Configuration — Database](../reference/configuration.md#database) for setup
instructions.

## Reverse proxy

ShoreGuard is designed to sit behind a reverse proxy like **nginx** or
**Caddy**. The proxy handles TLS termination and forwards traffic to
ShoreGuard over plain HTTP on localhost.

Example nginx snippet:

```nginx
server {
    listen 443 ssl;
    server_name shoreguard.example.com;

    ssl_certificate     /etc/ssl/certs/shoreguard.pem;
    ssl_certificate_key /etc/ssl/private/shoreguard.key;

    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8888;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## HTTPS

ShoreGuard does not terminate TLS itself. When it detects that the request
arrived via HTTPS (from the `X-Forwarded-Proto` header), it automatically sets
the `secure` flag on session cookies.

## Headless setup

For automated deployments where no browser is available for the setup wizard,
you can create the initial admin account from the command line:

```bash
# Option 1: environment variable (useful in Docker / CI)
SHOREGUARD_ADMIN_PASSWORD=secret shoreguard --no-reload

# Option 2: CLI command
shoreguard create-user admin@example.com --role admin
```

## Troubleshooting

See the [Troubleshooting guide](troubleshooting.md) for common issues and
solutions.
