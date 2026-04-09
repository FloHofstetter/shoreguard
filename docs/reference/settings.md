# ShoreGuard Settings Reference

Auto-generated from `shoreguard config schema --format markdown`. Every environment variable understood by ShoreGuard is listed below, grouped by the settings sub-model it belongs to.

## `server`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_HOST` | `0.0.0.0` | Bind address for the HTTP server |
| `SHOREGUARD_PORT` | `8888` | TCP port for the HTTP server |
| `SHOREGUARD_LOG_LEVEL` | `info` | Log level: critical\|error\|warning\|info\|debug\|trace |
| `SHOREGUARD_LOG_FORMAT` | `text` | Log output format — 'text' for humans, 'json' for aggregators |
| `SHOREGUARD_RELOAD` | `true` | Auto-reload on code changes (dev only) |
| `SHOREGUARD_DATABASE_URL` | `` | SQLAlchemy database URL (sqlite:/// or postgresql://). Unset falls back to sqlite in the XDG config dir. |
| `SHOREGUARD_LOCAL_MODE` | `false` | Allow private-IP targets in SSRF checks (local gateway dev) |
| `SHOREGUARD_GRACEFUL_SHUTDOWN_TIMEOUT` | `15` | Seconds uvicorn waits for in-flight requests on SIGTERM |
| `SHOREGUARD_GZIP_MINIMUM_SIZE` | `1000` | Minimum response body size in bytes before gzip compression kicks in |
| `SHOREGUARD_READYZ_TIMEOUT` | `5.0` | Timeout in seconds for /readyz dependency probes |
## `database`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_DB_POOL_SIZE` | `5` | SQLAlchemy connection pool size |
| `SHOREGUARD_DB_MAX_OVERFLOW` | `10` | Additional pool connections allowed above pool_size |
| `SHOREGUARD_DB_POOL_TIMEOUT` | `30` | Seconds to wait for a pool connection before failing |
| `SHOREGUARD_DB_POOL_RECYCLE` | `1800` | Seconds after which connections are recycled |
| `SHOREGUARD_DB_STATEMENT_TIMEOUT_MS` | `30000` | PostgreSQL statement_timeout in ms (applied per connection) |
| `SHOREGUARD_DB_STARTUP_RETRY_ATTEMPTS` | `10` | Number of times init_db() retries Alembic upgrade on OperationalError |
| `SHOREGUARD_DB_STARTUP_RETRY_DELAY` | `2.0` | Initial backoff in seconds between DB retry attempts |
| `SHOREGUARD_DB_STARTUP_RETRY_MAX_DELAY` | `30.0` | Maximum backoff cap in seconds between DB retry attempts |
## `auth`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_NO_AUTH` | `false` | Disable authentication entirely (development only) |
| `SHOREGUARD_SECRET_KEY` | `` | HMAC secret for sessions and signed cookies. Unset falls back to on-disk .secret_key — set explicitly for multi-replica. |
| `SHOREGUARD_ALLOW_REGISTRATION` | `false` | Allow unauthenticated self-signup via /register |
| `SHOREGUARD_ADMIN_PASSWORD` | `` | Bootstrap admin password used on first startup if no users exist |
| `SHOREGUARD_COOKIE_NAME` | `sg_session` | Session cookie name |
| `SHOREGUARD_SESSION_MAX_AGE` | `604800` | Session cookie lifetime in seconds (default: 7 days) |
| `SHOREGUARD_INVITE_MAX_AGE` | `604800` | Invite token validity in seconds (default: 7 days) |
| `SHOREGUARD_PASSWORD_MIN_LENGTH` | `8` | Minimum password length for user registration |
| `SHOREGUARD_PASSWORD_REQUIRE_COMPLEXITY` | `false` | Require mixed-case, digit, and symbol in passwords |
| `SHOREGUARD_LOGIN_RATE_LIMIT_ATTEMPTS` | `10` | Max failed login attempts per IP before rate limit kicks in |
| `SHOREGUARD_LOGIN_RATE_LIMIT_WINDOW` | `300` | Login rate-limit sliding window in seconds |
| `SHOREGUARD_LOGIN_RATE_LIMIT_LOCKOUT` | `900` | Login rate-limit lockout duration in seconds |
| `SHOREGUARD_ACCOUNT_LOCKOUT_ATTEMPTS` | `5` | Max failed logins per account before lockout |
| `SHOREGUARD_ACCOUNT_LOCKOUT_DURATION` | `900` | Account lockout duration in seconds after threshold |
| `SHOREGUARD_WRITE_RATE_LIMIT_ATTEMPTS` | `30` | Max write requests per IP before rate limit kicks in |
| `SHOREGUARD_WRITE_RATE_LIMIT_WINDOW` | `60` | Write rate-limit sliding window in seconds |
| `SHOREGUARD_WRITE_RATE_LIMIT_LOCKOUT` | `120` | Write rate-limit lockout duration in seconds |
| `SHOREGUARD_GLOBAL_RATE_LIMIT_ATTEMPTS` | `300` | Global per-IP rate limit (DDoS guardrail) |
| `SHOREGUARD_GLOBAL_RATE_LIMIT_WINDOW` | `60` | Global rate-limit sliding window in seconds |
| `SHOREGUARD_GLOBAL_RATE_LIMIT_LOCKOUT` | `60` | Global rate-limit lockout duration in seconds |
| `SHOREGUARD_METRICS_PUBLIC` | `false` | Expose /metrics without authentication (default: admin-only) |
| `SHOREGUARD_HSTS_ENABLED` | `false` | Emit Strict-Transport-Security header (enable behind HTTPS proxy) |
| `SHOREGUARD_HSTS_MAX_AGE` | `63072000` | HSTS max-age in seconds (default: 2 years) |
| `SHOREGUARD_CSP_POLICY` | `default-src 'self'; script-src 'self'...` | Content-Security-Policy header value |
## `gateway`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_GATEWAY_BACKOFF_MIN` | `5.0` | Initial reconnect backoff in seconds |
| `SHOREGUARD_GATEWAY_BACKOFF_MAX` | `60.0` | Maximum reconnect backoff in seconds |
| `SHOREGUARD_GATEWAY_BACKOFF_FACTOR` | `2.0` | Exponential backoff multiplier between attempts |
| `SHOREGUARD_GATEWAY_GRPC_TIMEOUT` | `30.0` | Default timeout for gRPC calls to gateways |
## `ops`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_OPS_MAX_RESULT_BYTES` | `65536` | Maximum bytes of operation result stored in DB (larger truncated) |
| `SHOREGUARD_OPS_RUNNING_TTL` | `600.0` | Seconds a running operation can go without a heartbeat before timeout |
| `SHOREGUARD_OPS_RETENTION_DAYS` | `30` | Days to retain completed operations before cleanup |
| `SHOREGUARD_OPS_FIELD_TRUNCATION_CHARS` | `8000` | Max characters per text field before truncation in operation records |
| `SHOREGUARD_OPS_MAX_LIST_LIMIT` | `200` | Maximum page size for /operations list queries |
## `audit`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_AUDIT_RETENTION_DAYS` | `90` | Days to retain audit log entries before cleanup |
| `SHOREGUARD_AUDIT_EXPORT_LIMIT` | `10000` | Maximum rows returned by /audit/export in a single call |
## `webhooks`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_WEBHOOK_DELIVERY_TIMEOUT` | `10.0` | HTTP request timeout for webhook delivery in seconds |
| `SHOREGUARD_WEBHOOK_RETRY_DELAYS` | `[5, 30, 120]` | Retry delays in seconds between failed webhook delivery attempts |
| `SHOREGUARD_WEBHOOK_DELIVERY_MAX_AGE_DAYS` | `7` | Days to retain webhook delivery records before cleanup |
## `background`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_BG_CLEANUP_INTERVAL` | `600` | Seconds between operation/audit cleanup passes |
| `SHOREGUARD_BG_CLEANUP_MAX_INTERVAL` | `900` | Maximum backoff cap for cleanup task after failures |
| `SHOREGUARD_BG_CLEANUP_BACKOFF_THRESHOLD` | `10` | Consecutive cleanup failures before entering backoff mode |
| `SHOREGUARD_BG_HEALTH_INTERVAL` | `30` | Seconds between gateway health probe cycles |
| `SHOREGUARD_BG_HEALTH_MAX_INTERVAL` | `300` | Maximum backoff cap for health monitor after failures |
| `SHOREGUARD_BG_HEALTH_BACKOFF_THRESHOLD` | `10` | Consecutive health probe failures before entering backoff mode |
## `local_gw`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_LOCAL_GW_STARTUP_RETRIES` | `10` | Times to retry probing a local gateway container during startup |
| `SHOREGUARD_LOCAL_GW_STARTUP_SLEEP` | `2.0` | Seconds to sleep between startup probe retries |
| `SHOREGUARD_LOCAL_GW_OPENSHELL_TIMEOUT` | `600.0` | Timeout in seconds for openshell subprocess calls |
| `SHOREGUARD_LOCAL_GW_DOCKER_TIMEOUT` | `30.0` | Timeout in seconds for docker subprocess calls (start, stop, inspect) |
| `SHOREGUARD_LOCAL_GW_STARTING_PORT` | `8080` | First port assigned to locally-spawned gateways |
## `websocket`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_WS_QUEUE_MAXSIZE` | `1000` | Maximum number of buffered events per WebSocket client |
| `SHOREGUARD_WS_QUEUE_GET_TIMEOUT` | `1.0` | Seconds to wait for an event before sending a heartbeat |
| `SHOREGUARD_WS_HEARTBEAT_INTERVAL` | `15.0` | Seconds between WebSocket heartbeat frames |
| `SHOREGUARD_WS_BACKPRESSURE_DROP_LIMIT` | `50` | Events dropped before a slow client is disconnected |
## `sandbox`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_SANDBOX_READY_TIMEOUT` | `180.0` | Seconds to wait for a sandbox to become ready before failing |
## `limits`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_LIMIT_MAX_CERT_BYTES` | `65536` | Maximum PEM certificate size in bytes |
| `SHOREGUARD_LIMIT_MAX_METADATA_JSON_BYTES` | `16384` | Maximum metadata JSON payload size in bytes |
| `SHOREGUARD_LIMIT_MAX_DESCRIPTION_LEN` | `1000` | Maximum free-text description length |
| `SHOREGUARD_LIMIT_MAX_LABELS` | `20` | Maximum label entries per resource |
| `SHOREGUARD_LIMIT_MAX_LABEL_VALUE_LEN` | `253` | Maximum label value length (DNS-style) |
| `SHOREGUARD_LIMIT_MAX_NAME_LEN` | `253` | Maximum resource name length (DNS-style) |
| `SHOREGUARD_LIMIT_MAX_URL_LEN` | `2048` | Maximum URL length in any field |
| `SHOREGUARD_LIMIT_MAX_API_KEY_LEN` | `512` | Maximum API key token length |
| `SHOREGUARD_LIMIT_MAX_EVENT_TYPES` | `50` | Maximum event types per webhook subscription |
| `SHOREGUARD_LIMIT_MAX_EVENT_TYPE_LEN` | `100` | Maximum event type string length |
| `SHOREGUARD_LIMIT_MAX_ENV_VARS` | `100` | Maximum environment variables per sandbox/command |
| `SHOREGUARD_LIMIT_MAX_ENV_KEY_LEN` | `256` | Maximum env var key length |
| `SHOREGUARD_LIMIT_MAX_ENV_VALUE_LEN` | `8192` | Maximum env var value length |
| `SHOREGUARD_LIMIT_MAX_CONFIG_ENTRIES` | `50` | Maximum config map entries per resource |
| `SHOREGUARD_LIMIT_MAX_CONFIG_VALUE_LEN` | `8192` | Maximum config map value length |
| `SHOREGUARD_LIMIT_MAX_COMMAND_LEN` | `65536` | Maximum command-line string length |
| `SHOREGUARD_LIMIT_MAX_REASON_LEN` | `1000` | Maximum audit reason text length |
| `SHOREGUARD_LIMIT_MAX_TIMEOUT_SECS` | `3600` | Maximum per-operation timeout requestable by API |
| `SHOREGUARD_LIMIT_MAX_IMAGE_LEN` | `512` | Maximum container image reference length |
| `SHOREGUARD_LIMIT_MAX_PASSWORD_LEN` | `128` | Maximum password length accepted (bcrypt 72-byte limit) |
| `SHOREGUARD_LIMIT_MAX_REQUEST_BODY_BYTES` | `10485760` | Maximum HTTP request body size in bytes (default: 10 MiB) |
## `oidc`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_OIDC_PROVIDERS_JSON` | `[]` | JSON array of OIDC provider configs (name, issuer, client_id, ...) |
| `SHOREGUARD_OIDC_DEFAULT_ROLE` | `viewer` | Role assigned to OIDC users whose claims do not match any mapping |
| `SHOREGUARD_OIDC_STATE_MAX_AGE` | `300` | Seconds an OIDC state cookie remains valid after authorize redirect |
## `cors`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_CORS_ALLOW_ORIGINS` | `[]` | Exact origins permitted by CORS (comma-separated via env var) |
| `SHOREGUARD_CORS_ALLOW_CREDENTIALS` | `true` | Allow cookies/authorization headers in CORS requests |
| `SHOREGUARD_CORS_ALLOW_METHODS` | `["*"]` | HTTP methods allowed by CORS (default: all) |
| `SHOREGUARD_CORS_ALLOW_HEADERS` | `["*"]` | Request headers allowed by CORS (default: all) |
| `SHOREGUARD_CORS_MAX_AGE` | `600` | CORS preflight cache duration in seconds |
