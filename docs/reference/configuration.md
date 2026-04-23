# Configuration

ShoreGuard is configured via **environment variables**. CLI flags override
environment variables, which override built-in defaults.

!!! tip "Complete list of settings"
    This page documents the common operational knobs. For the **complete,
    auto-generated reference of every `SHOREGUARD_*` environment variable**
    — including sub-models for audit, webhooks, rate limits, and more —
    see [settings.md](settings.md).

    You can also dump the current effective configuration live:

    ```bash
    shoreguard config show                   # all settings as a table
    shoreguard config show auth              # single section
    shoreguard config show --format json     # machine-readable
    shoreguard config show --show-sensitive  # reveal redacted values
    shoreguard config schema --format markdown  # regenerate settings.md
    ```

## Precedence

1. **CLI flags** — always win
2. **Environment variables**
3. **Built-in defaults**

---

## Server

| Variable | CLI Flag | Default | Description |
|----------|----------|---------|-------------|
| `SHOREGUARD_HOST` | `--host` | `0.0.0.0` | Bind address |
| `SHOREGUARD_PORT` | `--port` | `8888` | HTTP port |
| `SHOREGUARD_LOG_LEVEL` | `--log-level` | `info` | Log verbosity (`debug`, `info`, `warning`, `error`) |
| `SHOREGUARD_LOG_FORMAT` | — | `text` | Log output format (`text`, `json`) |
| `SHOREGUARD_RELOAD` | `--reload` / `--no-reload` | `true` | Auto-reload on source changes (disable in production with `--no-reload`) |
| `SHOREGUARD_DATABASE_URL` | `--database-url` | SQLite | Database connection string |
| `SHOREGUARD_LOCAL_MODE` | `--local` | `false` | Enable Docker-based gateway lifecycle management |
| `SHOREGUARD_GRACEFUL_SHUTDOWN_TIMEOUT` | — | `5` | Seconds to wait for in-flight requests during shutdown |
| `SHOREGUARD_GZIP_MINIMUM_SIZE` | — | `1000` | Minimum response size (bytes) before gzip compression kicks in |
| `SHOREGUARD_ALLOW_UNSAFE_CONFIG` | — | `false` | **Emergency override.** When `true`, `enforce_production_safety()` logs `ERROR:`-severity config issues at `CRITICAL` and continues startup instead of refusing. Use only to bring a broken stack up for debugging — every start-up under this flag leaves a loud audit trail in the logs. |

## Authentication & Sessions {: #auth }

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_NO_AUTH` | `false` | Disable all authentication (**development only**) |
| `SHOREGUARD_SECRET_KEY` | auto-generated | HMAC secret for session cookies. **Set this in production** — otherwise a random key is generated on each restart, invalidating all sessions |
| `SHOREGUARD_ALLOW_REGISTRATION` | `false` | Allow self-registration for new users (viewer role) |
| `SHOREGUARD_ADMIN_PASSWORD` | — | Bootstrap admin account password for headless setup (skip wizard) |
| `SHOREGUARD_COOKIE_NAME` | `sg_session` | Session cookie name |
| `SHOREGUARD_SESSION_MAX_AGE` | `604800` | Session cookie lifetime in seconds (default: 7 days) |
| `SHOREGUARD_INVITE_MAX_AGE` | `604800` | Invite token lifetime in seconds (default: 7 days) |
| `SHOREGUARD_PASSWORD_MIN_LENGTH` | `8` | Minimum password length |
| `SHOREGUARD_PASSWORD_REQUIRE_COMPLEXITY` | `false` | Require mixed case, digits, and special characters |
| `SHOREGUARD_LOGIN_RATE_LIMIT_ATTEMPTS` | `10` | Max login attempts per IP within the rate-limit window |
| `SHOREGUARD_LOGIN_RATE_LIMIT_WINDOW` | `300` | Rate-limit window in seconds (default: 5 min) |
| `SHOREGUARD_LOGIN_RATE_LIMIT_LOCKOUT` | `900` | IP lockout duration after exceeding the limit (default: 15 min) |
| `SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS` | `5` | Max failed attempts before an account is locked |
| `SHOREGUARD_ACCOUNT_LOCKOUT_DURATION` | `900` | Account lockout duration in seconds (default: 15 min) |
| `SHOREGUARD_METRICS_PUBLIC` | `false` | Expose `/metrics` without authentication |
| `SHOREGUARD_HSTS_ENABLED` | `false` | Send `Strict-Transport-Security` header |
| `SHOREGUARD_HSTS_MAX_AGE` | `63072000` | HSTS max-age in seconds (default: 2 years) |
| `SHOREGUARD_CSP_STRICT` | `true` | Enforce strict CSP with per-request nonce and no `'unsafe-inline'` (default since v0.27.0) |
| `SHOREGUARD_CSP_POLICY` | *(legacy, see below)* | Content-Security-Policy header value used only when `SHOREGUARD_CSP_STRICT=false` |

Default CSP policy (strict, since v0.27.0):

```
default-src 'self'; script-src 'self' 'nonce-<per-request>' 'unsafe-eval' https://cdn.jsdelivr.net;
style-src 'self' https://cdn.jsdelivr.net;
font-src 'self' https://cdn.jsdelivr.net; img-src 'self' data:; connect-src 'self' wss:;
frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

Every response carries a fresh cryptographic nonce; all inline `<script>` tags
rendered by ShoreGuard templates carry that nonce. No `'unsafe-inline'`, and
`frame-ancestors 'none'` blocks clickjacking. `'unsafe-eval'` is retained
because Alpine.js uses the `Function()` constructor internally; the
`@alpinejs/csp` build was evaluated during v0.27.0 development but its
expression parser is too restrictive for this UI (plain property chains only,
no operators, literals, or method-call arguments). Unlike `'unsafe-inline'`,
`'unsafe-eval'` does not permit DOM-injected script execution, so the XSS
surface remains dramatically smaller than the legacy policy.

**Legacy opt-out.** If you ship custom templates with unverified inline
scripts or third-party embeds, set `SHOREGUARD_CSP_STRICT=false` to fall back
to the pre-v0.27.0 policy:

```
default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net;
style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net;
font-src 'self' https://cdn.jsdelivr.net; img-src 'self' data:; connect-src 'self' wss:
```

`SHOREGUARD_CSP_POLICY` overrides the legacy template when set; it has no
effect when strict mode is on.

## OIDC / SSO {: #oidc }

See the [OIDC / SSO guide](../admin/oidc.md) for setup instructions.

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_OIDC_PROVIDERS_JSON` | `[]` | JSON array of OIDC provider configurations |
| `SHOREGUARD_OIDC_DEFAULT_ROLE` | `viewer` | Role assigned to new OIDC users without a role mapping match |
| `SHOREGUARD_OIDC_STATE_MAX_AGE` | `300` | State cookie TTL in seconds (default: 5 min) |

## Gateway Connection {: #gateway }

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_GATEWAY_BACKOFF_MIN` | `5.0` | Minimum backoff delay (seconds) between gateway reconnect attempts |
| `SHOREGUARD_GATEWAY_BACKOFF_MAX` | `60.0` | Maximum backoff delay (seconds) |
| `SHOREGUARD_GATEWAY_BACKOFF_FACTOR` | `2.0` | Exponential backoff multiplier |
| `SHOREGUARD_GATEWAY_GRPC_TIMEOUT` | `30.0` | Default gRPC call timeout (seconds) |

## Long-Running Operations {: #operations }

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_OPS_MAX_RESULT_BYTES` | `65536` | Maximum stored result size per operation |
| `SHOREGUARD_OPS_RUNNING_TTL` | `600.0` | TTL for in-progress operations before they are marked stale (seconds) |
| `SHOREGUARD_OPS_RETENTION_DAYS` | `30` | Days to retain completed operation records |
| `SHOREGUARD_OPS_FIELD_TRUNCATION_CHARS` | `8000` | Max characters per field in operation results |
| `SHOREGUARD_OPS_MAX_LIST_LIMIT` | `200` | Maximum number of operations returned by list queries |

## Audit Log {: #audit }

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_AUDIT_RETENTION_DAYS` | `90` | Days to retain audit log entries |
| `SHOREGUARD_AUDIT_EXPORT_LIMIT` | `10000` | Maximum rows per audit export request |

## Webhooks {: #webhooks }

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_WEBHOOK_DELIVERY_TIMEOUT` | `10.0` | HTTP timeout for webhook delivery (seconds) |
| `SHOREGUARD_WEBHOOK_RETRY_DELAYS` | `[5, 30, 120]` | Retry delays in seconds (JSON array) |
| `SHOREGUARD_WEBHOOK_DELIVERY_MAX_AGE_DAYS` | `7` | Days to retain delivery records |

## Background Tasks {: #background }

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_BG_CLEANUP_INTERVAL` | `600` | Cleanup task interval (seconds) |
| `SHOREGUARD_BG_CLEANUP_MAX_INTERVAL` | `900` | Max interval after backoff |
| `SHOREGUARD_BG_CLEANUP_BACKOFF_THRESHOLD` | `10` | Consecutive errors before backing off |
| `SHOREGUARD_BG_HEALTH_INTERVAL` | `30` | Gateway health-check interval (seconds) |
| `SHOREGUARD_BG_HEALTH_MAX_INTERVAL` | `300` | Max health-check interval after backoff |
| `SHOREGUARD_BG_HEALTH_BACKOFF_THRESHOLD` | `10` | Consecutive errors before backing off |

## Cert Rotation {: #cert-rotation }

Proactive mTLS client-cert rotation. When enabled (default), a background
task polls every registered gateway's cert expiry; certs below the
threshold are rotated by re-reading credentials from the registry and
rebuilding the gRPC channel via `reload_credentials()`. See
[Cert Rotation operations](../operations/cert-rotation.md) for the
runbook.

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_CERT_ROTATION_ENABLED` | `true` | Master switch for the rotation loop |
| `SHOREGUARD_CERT_ROTATION_THRESHOLD_DAYS` | `7` | Rotate when remaining validity drops below this many days |
| `SHOREGUARD_CERT_ROTATION_POLL_INTERVAL_S` | `3600` | Seconds between rotation poll cycles |
| `SHOREGUARD_CERT_ROTATION_MAX_RETRIES` | `3` | Retry attempts per rotation before firing the `gateway.cert_rotation_failed` webhook |

## Local Gateway {: #local-gateway }

Only relevant when `SHOREGUARD_LOCAL_MODE=true`. See the
[local mode guide](../admin/local-mode.md).

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_LOCAL_GW_STARTUP_RETRIES` | `10` | Retry attempts when starting a gateway container |
| `SHOREGUARD_LOCAL_GW_STARTUP_SLEEP` | `2.0` | Seconds between startup retries |
| `SHOREGUARD_LOCAL_GW_OPENSHELL_TIMEOUT` | `600.0` | Timeout for OpenShell CLI commands (seconds) |
| `SHOREGUARD_LOCAL_GW_DOCKER_TIMEOUT` | `30.0` | Timeout for Docker API calls (seconds) |
| `SHOREGUARD_LOCAL_GW_STARTING_PORT` | `8080` | First port to assign to local gateway containers |

## WebSocket {: #websocket }

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_WS_QUEUE_MAXSIZE` | `1000` | Maximum events buffered per WebSocket connection |
| `SHOREGUARD_WS_QUEUE_GET_TIMEOUT` | `1.0` | Seconds to wait for the next event before sending a heartbeat |
| `SHOREGUARD_WS_HEARTBEAT_INTERVAL` | `15.0` | Heartbeat ping interval (seconds) |
| `SHOREGUARD_WS_BACKPRESSURE_DROP_LIMIT` | `50` | Drop oldest events when the queue exceeds this threshold |

## Sandbox {: #sandbox }

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_SANDBOX_READY_TIMEOUT` | `180.0` | Seconds to wait for a sandbox to become ready after creation |

## Input Limits {: #limits }

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOREGUARD_LIMIT_MAX_CERT_BYTES` | `65536` | Maximum size of uploaded certificates (bytes) |
| `SHOREGUARD_LIMIT_MAX_METADATA_JSON_BYTES` | `16384` | Maximum size of gateway metadata JSON (bytes) |
| `SHOREGUARD_LIMIT_MAX_DESCRIPTION_LEN` | `1000` | Maximum gateway description length (characters) |
| `SHOREGUARD_LIMIT_MAX_LABELS` | `20` | Maximum number of labels per gateway |
| `SHOREGUARD_LIMIT_MAX_LABEL_VALUE_LEN` | `253` | Maximum label value length (characters) |

---

## Database {: #database }

### SQLite (default)

ShoreGuard creates a SQLite database at `~/.config/shoreguard/shoreguard.db`
on first run. No setup required — works well for single-node deployments and
local development.

### PostgreSQL

For multi-replica or production deployments, pass a PostgreSQL connection
string:

```bash
export SHOREGUARD_DATABASE_URL="postgresql+psycopg://user:pass@db-host:5432/shoreguard"
shoreguard --no-reload
```

The database and tables are created automatically on first start. Migrations
are applied on every startup via Alembic. See the
[database migrations runbook](../admin/database-migrations.md) for manual
migration and backup procedures.
