# Configuration

## Environment variables

| Variable | CLI Flag | Default | Description |
|----------|----------|---------|-------------|
| `SHOREGUARD_HOST` | `--host` | `0.0.0.0` | Bind address |
| `SHOREGUARD_PORT` | `--port` | `8888` | HTTP port |
| `SHOREGUARD_LOG_LEVEL` | `--log-level` | `info` | Log verbosity (`debug`, `info`, `warning`, `error`) |
| `SHOREGUARD_RELOAD` | `--reload` / `--no-reload` | `true` | Auto-reload on source changes |
| `SHOREGUARD_LOCAL_MODE` | `--local` | — | Enable Docker-based gateway lifecycle |
| `SHOREGUARD_NO_AUTH` | `--no-auth` | — | Disable authentication (development only) |
| `SHOREGUARD_DATABASE_URL` | `--database-url` | SQLite | Database connection string |
| `SHOREGUARD_API_KEY` | `--api-key` | — | Legacy shared API key |
| `SHOREGUARD_ALLOW_REGISTRATION` | — | — | Enable self-registration for new users |
| `SHOREGUARD_ADMIN_PASSWORD` | — | — | Bootstrap admin password for headless setup |
| `SHOREGUARD_SECRET_KEY` | — | auto-generated | HMAC secret used for session cookies |

## Precedence

Configuration values are resolved in the following order (highest to lowest):

1. **CLI flags** — always win
2. **Environment variables**
3. **Built-in defaults**

## Database

### SQLite (default)

By default ShoreGuard creates a SQLite database at:

```
~/.config/shoreguard/shoreguard.db
```

No extra setup is required. SQLite is a good fit for single-node deployments
and local development.

### PostgreSQL

For multi-replica or production deployments, pass a PostgreSQL connection
string:

```bash
export SHOREGUARD_DATABASE_URL="postgresql+asyncpg://user:pass@db-host:5432/shoreguard"
shoreguard
```

The database and tables are created automatically on first start.
