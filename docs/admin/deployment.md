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
export SHOREGUARD_DATABASE_URL="postgresql+asyncpg://user:pass@db-host:5432/shoreguard"
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
