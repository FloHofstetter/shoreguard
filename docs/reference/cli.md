# CLI Reference

## Server command

```bash
shoreguard [OPTIONS]
```

Starts the ShoreGuard control-plane server.

| Flag | Env variable | Default | Description |
|------|-------------|---------|-------------|
| `--host` | `SHOREGUARD_HOST` | `0.0.0.0` | Bind address |
| `--port` | `SHOREGUARD_PORT` | `8888` | HTTP port |
| `--log-level` | `SHOREGUARD_LOG_LEVEL` | `info` | Log verbosity (`debug`, `info`, `warning`, `error`) |
| `--reload` / `--no-reload` | `SHOREGUARD_RELOAD` | reload on | Auto-reload on file changes |
| `--local` / `--no-local` | `SHOREGUARD_LOCAL_MODE` | off | Enable Docker-based gateway lifecycle |
| `--no-auth` / `--auth` | `SHOREGUARD_NO_AUTH` | auth on | Disable authentication (development only) |
| `--database-url` | `SHOREGUARD_DATABASE_URL` | SQLite | Database connection string |
| `--version` | — | — | Print version and exit |

CLI flags take priority over environment variables, which take priority over
built-in defaults.

## Management commands

Management commands operate directly on the database and do not require the
server to be running. All of them accept `--database-url` to target a specific
database.

### User management

```bash
# Create a user (prompts for password if --password is omitted)
shoreguard create-user alice@example.com --role operator --password s3cret

# List all users
shoreguard list-users

# Delete a user by email
shoreguard delete-user alice@example.com
```

### Service principals

Service principals are non-human accounts used for API and Terraform access.
Creating one prints an API key that cannot be retrieved again.

```bash
# Create a service principal
shoreguard create-service-principal ci-deploy --role operator

# List service principals
shoreguard list-service-principals
```

### Gateway import

Import gateways from a legacy OpenShell config directory. See
[Migrating from v0.2](../getting-started/migration.md) for details.

```bash
shoreguard import-gateways
```
