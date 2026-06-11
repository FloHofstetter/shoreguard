# ShoreGuard Settings Reference

Auto-generated from `shoreguard config schema --format markdown`. Every environment variable understood by ShoreGuard is listed below, grouped by the settings sub-model it belongs to.

## `server`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_HOST` | `0.0.0.0` | Bind address for the HTTP server |
| `SHOREGUARD_PORT` | `8888` | TCP port for the HTTP server |
| `SHOREGUARD_PUBLIC_URL` | `` | Externally reachable base URL of this ShoreGuard (e.g. https://spark.tail1234.ts.net). Used to build absolute links in notifications; required for one-tap approval links. |
| `SHOREGUARD_LOG_LEVEL` | `info` | Log level: critical\|error\|warning\|info\|debug\|trace |
| `SHOREGUARD_LOG_FORMAT` | `text` | Log output format — 'text' for humans, 'json' for aggregators |
| `SHOREGUARD_RELOAD` | `true` | Auto-reload on code changes (dev only) |
| `SHOREGUARD_DATABASE_URL` | `` | SQLAlchemy database URL (sqlite:/// or postgresql://). Unset falls back to sqlite in the XDG config dir. |
| `SHOREGUARD_LOCAL_MODE` | `false` | Allow private-IP targets in SSRF checks (local gateway dev) |
| `SHOREGUARD_GRACEFUL_SHUTDOWN_TIMEOUT` | `15` | Seconds uvicorn waits for in-flight requests on SIGTERM |
| `SHOREGUARD_GZIP_MINIMUM_SIZE` | `1000` | Minimum response body size in bytes before gzip compression kicks in |
| `SHOREGUARD_READYZ_TIMEOUT` | `5.0` | Timeout in seconds for /readyz dependency probes |
| `SHOREGUARD_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Comma-separated IPs (or '*') whose X-Forwarded-* headers uvicorn trusts. Set to '*' when serving behind a k8s Ingress — the default only trusts loopback, which means TLS-terminating proxies are ignored. |
| `SHOREGUARD_ALWAYS_BLOCKED_IPS` | `` | Comma-separated IPs or CIDR ranges that are always blocked as SSRF targets regardless of local_mode. Mirrors upstream OpenShell #814. Parsed once at startup; an invalid entry hard-fails boot. |
| `SHOREGUARD_SSRF_ALLOWED_IPS` | `` | Comma-separated IPs or CIDR ranges exempted from the private/loopback SSRF rejection — e.g. a homelab OIDC provider or webhook target on a LAN address. Matched against the resolved address, so hostnames are exempt only if they resolve into an allowlisted range. SHOREGUARD_ALWAYS_BLOCKED_IPS takes precedence. Parsed once at startup; an invalid entry hard-fails boot. |
| `SHOREGUARD_UNSAFE_LAN` | `false` | Allow serving without authentication (SHOREGUARD_NO_AUTH) on a non-loopback bind address. Off by default — an unauthenticated UI on a network interface gives everyone on that network admin access. |
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
| `SHOREGUARD_SINGLE_USER` | `false` | Single-user mode: one admin account whose password is SHOREGUARD_ADMIN_PASSWORD, kept in sync on every startup. The homelab middle ground between --no-auth and full RBAC. |
| `SHOREGUARD_TAILSCALE_IDENTITY` | `false` | Trust Tailscale Serve identity headers (Tailscale-User-Login) from a loopback proxy as authentication. The login must match an existing user's email. Only honoured for connections from 127.0.0.1/::1 — i.e. `tailscale serve` in front of a loopback-bound ShoreGuard. |
| `SHOREGUARD_PASSKEYS_ENABLED` | `true` | Enable WebAuthn passkey registration and login (requires HTTPS or localhost in the browser) |
| `SHOREGUARD_PASSKEY_RP_ID` | `` | Override the WebAuthn relying-party ID; defaults to the host of SHOREGUARD_PUBLIC_URL, else the request host |
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
| `SHOREGUARD_CSP_POLICY` | `default-src 'self'; script-src 'self'...` | Content-Security-Policy header value (used when csp_strict=False) |
| `SHOREGUARD_CSP_STRICT` | `true` | Enforce strict CSP with per-request nonce, no 'unsafe-inline', and frame-ancestors 'none'. Default as of v0.27.0 — blocks inline scripts, inline event handlers, and inline styles. Since the Preact islands rewrite removed Alpine.js, 'unsafe-eval' is no longer needed. Set SHOREGUARD_CSP_STRICT=false to fall back to the legacy 'unsafe-inline' policy in `csp_policy`. |
| `SHOREGUARD_CSP_POLICY_STRICT` | `default-src 'self'; script-src 'self'...` | CSP template used when csp_strict=True. Must contain a '{nonce}' placeholder that is replaced per-request. |
## `gateway`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_GATEWAY_BACKOFF_MIN` | `5.0` | Initial reconnect backoff in seconds |
| `SHOREGUARD_GATEWAY_BACKOFF_MAX` | `60.0` | Maximum reconnect backoff in seconds |
| `SHOREGUARD_GATEWAY_BACKOFF_FACTOR` | `2.0` | Exponential backoff multiplier between attempts |
| `SHOREGUARD_GATEWAY_GRPC_TIMEOUT` | `30.0` | Default timeout for gRPC calls to gateways |
| `SHOREGUARD_GATEWAY_GRPC_RETRY_MAX_ATTEMPTS` | `4` | Maximum number of attempts (including the first) for a retryable gRPC call |
| `SHOREGUARD_GATEWAY_GRPC_RETRY_INITIAL_BACKOFF` | `0.25` | Initial exponential backoff between retries in seconds |
| `SHOREGUARD_GATEWAY_GRPC_RETRY_MAX_BACKOFF` | `4.0` | Maximum exponential backoff between retries in seconds |
| `SHOREGUARD_GATEWAY_GRPC_RETRY_DEADLINE` | `60.0` | Total wall-clock budget in seconds for a single logical RPC including all retries. Retries will not exceed this deadline. |
| `SHOREGUARD_GATEWAY_REQUIRE_MTLS` | `true` | Reject plaintext gRPC channels to gateways. Disable only for local development against an insecure gateway. |
| `SHOREGUARD_GATEWAY_CERT_EXPIRY_WARN_DAYS` | `14` | Warn (but do not reject) when a gateway certificate expires within this many days. A structured log warning is emitted per affected channel. |
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
| `SHOREGUARD_AUDIT_EXPORT_STDOUT_JSON` | `false` | Emit each audit entry as a JSON line on stdout (Loki/Vector lane) |
| `SHOREGUARD_AUDIT_EXPORT_SYSLOG_ENABLED` | `false` | Ship each audit entry to a remote syslog receiver as JSON body |
| `SHOREGUARD_AUDIT_EXPORT_SYSLOG_HOST` | `localhost` | Syslog server host when export_syslog_enabled=true |
| `SHOREGUARD_AUDIT_EXPORT_SYSLOG_PORT` | `514` | Syslog server port when export_syslog_enabled=true |
| `SHOREGUARD_AUDIT_EXPORT_SYSLOG_FACILITY` | `user` | Syslog facility name (user, local0..local7, daemon, ...) |
| `SHOREGUARD_AUDIT_EXPORT_WEBHOOK_ENABLED` | `false` | Bridge audit entries into the existing webhook pipeline as 'audit.entry' events; individual targets are configured per Webhook record |
## `webhooks`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_WEBHOOK_DELIVERY_TIMEOUT` | `10.0` | HTTP request timeout for webhook delivery in seconds |
| `SHOREGUARD_WEBHOOK_RETRY_DELAYS` | `[5, 30, 120]` | Retry delays in seconds between failed webhook delivery attempts |
| `SHOREGUARD_WEBHOOK_DELIVERY_MAX_AGE_DAYS` | `7` | Days to retain webhook delivery records before cleanup |
| `SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS` | `false` | Attach signed one-tap approve/reject links to approval webhook events. Anyone holding such a link can cast that one vote until it expires — treat notification channels accordingly. Requires SHOREGUARD_PUBLIC_URL. |
| `SHOREGUARD_WEBHOOK_ONE_TAP_TTL` | `3600` | Validity window of one-tap approval links in seconds |
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
## `prover`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_PROVER_TIMEOUT_MS` | `5000` | Z3 solver timeout per query in milliseconds |
| `SHOREGUARD_PROVER_MAX_QUERIES_PER_REQUEST` | `10` | Maximum queries per verify request |
| `SHOREGUARD_PROVER_ENABLED` | `true` | Enable/disable the prover feature |
## `discovery`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_DISCOVERY_ENABLED` | `false` | Enable the gateway discovery background loop |
| `SHOREGUARD_DISCOVERY_DOMAINS` | `[]` | Base domains to scan for `_openshell._tcp` SRV records |
| `SHOREGUARD_DISCOVERY_INTERVAL_SECONDS` | `300` | Background re-scan interval in seconds (>= 30) |
| `SHOREGUARD_DISCOVERY_DEFAULT_SCHEME` | `grpc+tls` | Connection scheme assigned to auto-registered gateways |
| `SHOREGUARD_DISCOVERY_AUTO_REGISTER` | `true` | If false, discovery only lists endpoints without registering |
| `SHOREGUARD_DISCOVERY_RESOLVER_TIMEOUT_SECONDS` | `5.0` | Per-query DNS resolver timeout in seconds |
| `SHOREGUARD_DISCOVERY_MDNS_ENABLED` | `false` | Also browse mDNS/zeroconf (`_openshell._tcp.local.`) during discovery scans — finds gateways on the local network without any DNS server (homelab) |
| `SHOREGUARD_DISCOVERY_MDNS_TIMEOUT_SECONDS` | `3.0` | How long an mDNS browse listens for announcements |
## `drift_detection`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_DRIFT_DETECTION_ENABLED` | `false` | Enable the policy drift detection background loop |
| `SHOREGUARD_DRIFT_DETECTION_INTERVAL_SECONDS` | `300` | Re-scan interval in seconds (>= 60) |
## `tracing`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_TRACING_ENABLED` | `false` | Enable OpenTelemetry auto-instrumentation for FastAPI and gRPC client |
| `SHOREGUARD_TRACING_SERVICE_NAME` | `shoreguard` | service.name resource attribute attached to every span |
| `SHOREGUARD_TRACING_OTLP_ENDPOINT` | `` | OTLP/HTTP traces endpoint URL; if unset, spans go to stdout console exporter |
| `SHOREGUARD_TRACING_SAMPLE_RATIO` | `1.0` | Head-based sampling ratio between 0.0 (off) and 1.0 (all) |
## `cert_rotation`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_CERT_ROTATION_ENABLED` | `true` | Enable the background proactive cert-rotation service |
| `SHOREGUARD_CERT_ROTATION_THRESHOLD_DAYS` | `7` | Rotate when remaining validity drops below this many days |
| `SHOREGUARD_CERT_ROTATION_POLL_INTERVAL_S` | `3600` | Seconds between rotation-check passes across gateways |
| `SHOREGUARD_CERT_ROTATION_MAX_RETRIES` | `3` | Retry attempts per rotation before deferring to the next cycle |
## `digest`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_DIGEST_ENABLED` | `false` | Dispatch a daily digest.daily webhook event summarising the last 24h (audit activity, sandbox churn, approvals, gateway health, webhook failures) |
| `SHOREGUARD_DIGEST_HOUR` | `7` | Local hour of day after which the daily digest is sent |
| `SHOREGUARD_DIGEST_CHECK_INTERVAL` | `600` | Seconds between digest due-checks by the background task |
## `budget`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_BUDGET_METERING_ENABLED` | `false` | Meter per-sandbox inference usage by polling gateway logs (phase 1 — replaced by the upstream metering RPC when it lands). Required for budgets and the usage/spend views. |
| `SHOREGUARD_BUDGET_INTERVAL_SECONDS` | `60` | Usage metering poll interval in seconds |
| `SHOREGUARD_BUDGET_INFERENCE_SOURCES` | `["inference", "proxy"]` | Log source substrings counted as inference requests |
| `SHOREGUARD_BUDGET_LOG_BATCH_LINES` | `2000` | Maximum log lines fetched per sandbox per metering poll |
## `smtp`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_SMTP_HOST` | `` | Default SMTP relay host for email webhooks. When set, email webhooks only need to_addrs in their extra_config; per-webhook smtp_host still overrides. |
| `SHOREGUARD_SMTP_PORT` | `587` | Default SMTP port |
| `SHOREGUARD_SMTP_USERNAME` | `` | Default SMTP username |
| `SHOREGUARD_SMTP_PASSWORD` | `` | Default SMTP password (secret) |
| `SHOREGUARD_SMTP_FROM_ADDR` | `shoreguard@localhost` | Default From address for email webhooks |
## `node_alert`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_NODE_ALERT_ENABLED` | `true` | Evaluate host thresholds periodically and fire node.threshold_breached / node.recovered webhook events on transitions (no-op unless a webhook subscribes) |
| `SHOREGUARD_NODE_ALERT_INTERVAL_SECONDS` | `60` | Seconds between host threshold evaluations |
| `SHOREGUARD_NODE_ALERT_GPU_TEMP_C` | `85.0` | GPU temperature breach threshold in degrees Celsius |
| `SHOREGUARD_NODE_ALERT_DISK_USED_PCT` | `90.0` | Root-disk usage breach threshold in percent |
| `SHOREGUARD_NODE_ALERT_MEM_USED_PCT` | `95.0` | Host memory usage breach threshold in percent |
## `push`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_PUSH_CONTACT` | `mailto:admin@localhost` | VAPID contact claim (mailto: or https:) sent to browser push services with each notification |
## `curfew`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_CURFEW_ENABLED` | `true` | Evaluate per-gateway curfews periodically (no-op unless a curfew is configured) |
| `SHOREGUARD_CURFEW_CHECK_INTERVAL` | `60` | Seconds between curfew window evaluations |
## `backup`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_BACKUP_ENABLED` | `false` | Periodically snapshot the SQLite DB + key material into a tar.gz archive (SHOREGUARD_BACKUP_DIR) |
| `SHOREGUARD_BACKUP_INTERVAL_HOURS` | `24` | Hours between periodic backup snapshots |
| `SHOREGUARD_BACKUP_DIR` | `` | Backup target directory (default: <config-dir>/backups) |
| `SHOREGUARD_BACKUP_KEEP` | `7` | Newest backup archives to retain during rotation |
## `updates`

| Environment variable | Default | Description |
|---|---|---|
| `SHOREGUARD_UPDATES_ENABLED` | `false` | Periodically check PyPI for a newer ShoreGuard release and fire a shoreguard.update_available webhook event (off by default — no phone-home without opt-in) |
| `SHOREGUARD_UPDATES_INTERVAL_HOURS` | `24` | Hours between update checks |
| `SHOREGUARD_UPDATES_URL` | `https://pypi.org/pypi/shoreguard/json` | Release index JSON endpoint queried by the update check |

