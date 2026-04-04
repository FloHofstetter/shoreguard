# Deployment

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

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_PASSWORD` | Yes | — | PostgreSQL password |
| `SHOREGUARD_SECRET_KEY` | Yes | — | HMAC secret for session cookies |
| `SHOREGUARD_ADMIN_PASSWORD` | No | — | Bootstrap admin account (skip wizard) |
| `SHOREGUARD_LOG_LEVEL` | No | `info` | `debug`, `info`, `warning`, `error` |
| `SHOREGUARD_PORT` | No | `8888` | Host port mapping |
| `SHOREGUARD_ALLOW_REGISTRATION` | No | `false` | Allow self-registration |
| `SHOREGUARD_HOST` | No | `0.0.0.0` | Network interface to listen on |
| `SHOREGUARD_DATABASE_URL` | No | SQLite | SQLAlchemy database URL (set automatically in Docker Compose) |
| `SHOREGUARD_LOCAL_MODE` | No | `false` | Enable Docker gateway lifecycle management |
| `SHOREGUARD_NO_AUTH` | No | `false` | Disable authentication (development only) |

See the [Configuration](../reference/configuration.md) page for details.

### Health probes

Health probes for container orchestration:

- `GET /healthz` — liveness (process running)
- `GET /readyz` — readiness (database reachable, services initialised)

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

**1. Pull the latest changes and rebuild:**

```bash
git pull
docker compose build
docker compose up -d
```

**2. Database migrations** are applied automatically on startup. No manual
migration step is needed.

**3. Verify** with the readiness probe:

```bash
curl -s http://localhost:8888/readyz | python3 -m json.tool
```

> [!NOTE]
> Always back up your database before upgrading. If something goes wrong,
> restore from the backup and check the [changelog](../changelog.md) for
> breaking changes.

## Production flags

```bash
shoreguard --no-reload --host 127.0.0.1
```

Binding to `127.0.0.1` ensures ShoreGuard only accepts connections from
localhost. Use a reverse proxy to expose it to the network.

## Database

### SQLite (default)

ShoreGuard creates a SQLite database at `~/.config/shoreguard/shoreguard.db`
on first run. No setup required — this works well for single-node deployments.

### PostgreSQL

For multi-instance or high-availability setups, point ShoreGuard at PostgreSQL:

```bash
export SHOREGUARD_DATABASE_URL="postgresql+psycopg://user:pass@db-host:5432/shoreguard"
shoreguard --no-reload
```

Migrations are applied automatically on startup.

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

### Port already in use

```
Error: bind: address already in use
```

Another process is using port 8888. Either stop it or change the port:

```bash
SHOREGUARD_PORT=9999 docker compose up -d
```

### Database connection refused

```
sqlalchemy.exc.OperationalError: connection refused
```

The PostgreSQL container may not be ready yet. Check its health:

```bash
docker compose ps
docker compose logs db
```

Wait for the `db` service to show `healthy` status.

### Docker socket permission denied

```
permission denied while trying to connect to the Docker daemon socket
```

Add your user to the `docker` group:

```bash
sudo usermod -aG docker $USER
# Log out and back in for the change to take effect
```

### Healthcheck failing

```bash
# Check ShoreGuard logs
docker compose logs shoreguard

# Test readiness manually
curl -s http://localhost:8888/readyz | python3 -m json.tool
```

Common causes: database not reachable, missing `SHOREGUARD_SECRET_KEY`,
or the application failed to start (check logs for Python tracebacks).
