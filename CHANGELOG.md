# Changelog

All notable changes to Shoreguard are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.4.0] — 2026-03-30

### Added

- **User-based RBAC** — three-tier role hierarchy (admin → operator → viewer)
  replaces the single shared API key. Users authenticate with email + password
  via session cookies; service principals use Bearer tokens for API/CI access.
- **Invite flow** — admins invite users by email. The invite generates a
  single-use, time-limited token (7 days). The invitee sets their password on
  the `/invite` page and receives a session cookie.
- **Self-registration** — opt-in via `SHOREGUARD_ALLOW_REGISTRATION=1`.
  New users register as viewers. Disabled by default.
- **Setup wizard** — first-run `/setup` page creates the initial admin account.
  All API access is blocked until setup is complete.
- **Service principals** — named API keys with roles, created by admins.
  Keys are SHA-256 hashed (never stored in plaintext). `last_used` timestamp
  tracked on each request.
- **User management UI** — `/users` page for admins with invite form, role
  badges, and delete actions. Dedicated `/users/new` and
  `/users/new-service-principal` pages replace the old modal dialogs.
- **Error pages** — styled error pages for 403, 404, and other HTTP errors
  instead of raw JSON responses in the browser.
- **User email in navbar** — logged-in user email and role badge shown in the
  navigation bar.
- **Alembic migrations 002–004** — `api_keys` table, `users` +
  `service_principals` tables with FK constraints, invite token hashing.
- **CLI commands** — `create-user`, `delete-user`, `list-users`,
  `create-service-principal`, `delete-service-principal`, `list-service-principals`.
- **710 tests** (up from 635), including comprehensive RBAC, auth flow,
  invite expiry, self-deletion guard, and last-admin protection tests.

### Changed

- **Auth module rewritten** — `shoreguard/api/auth.py` expanded from ~100
  to ~700 lines. Session tokens are HMAC-signed with a 5-part format
  (`nonce.expiry.user_id.role.signature`). Roles are always verified against
  the database, not the session token, so demotions take effect immediately.
- **All state-changing endpoints** now enforce minimum role via
  `require_role()` FastAPI dependency (admin for user/SP management and
  gateway registration; operator for sandbox/policy/provider operations).
- **Frontend role-based UI** — buttons and nav items hidden based on role
  via `data-sg-min-role` attributes. `escapeHtml()` used consistently
  across all JavaScript files.
- **Policies router split** — preset routes (`/api/policies/presets`) are
  mounted globally; sandbox policy routes remain gateway-scoped only.
  Fixes a bug where `/api/sandboxes/{name}/policy` was reachable without
  gateway context.
- **Audit logging standardised** — all log messages use `actor=` consistently.
  Role denials now include method, path, and actor. IntegrityError on
  duplicate user/SP creation is logged. Logout resolves email instead of
  numeric user ID.

### Fixed

- **Timing attack in `authenticate_user()`** — bcrypt verification now runs
  against a dummy hash when the user does not exist, preventing email
  enumeration via response time analysis.
- **Policies router double-inclusion** — the full policies router was mounted
  both globally and under the gateway prefix, exposing sandbox policy routes
  without gateway context. Now only preset routes are global.
- **Missing exception handling** — `is_setup_complete()`, `list_users()`, and
  `list_service_principals()` now catch `SQLAlchemyError` instead of letting
  database errors propagate as 500s.
- **`verify_password()` bare Exception catch** — narrowed to
  `(ValueError, TypeError)` to avoid masking unexpected errors.
- **WebSocket XSS** — `sandboxName` in toast messages is now escaped with
  `escapeHtml()`. Log level CSS class validated against a whitelist.
- **`delete_filesystem_path` missing Query annotation** — `path` parameter
  now uses explicit `Query(...)` instead of relying on FastAPI inference.
- **Migration 004 downgrade** documented as non-reversible (SHA-256 hashes
  cannot be reversed; pending invites are invalidated on downgrade).

### Security

- Constant-time authentication prevents timing-based email enumeration.
- Invite tokens are SHA-256 hashed in the database (migration 004).
- Session invalidation on user deletion and deactivation — existing sessions
  are rejected on the next request.
- Last-admin guard with database-level `FOR UPDATE` lock prevents TOCTOU race.
- Self-deletion guard prevents admins from deleting their own account.
- Email normalisation (`.strip().lower()`) prevents duplicate accounts.
- Password length enforced (8–128 characters) on all auth endpoints.
- XSS escaping hardened across all frontend JavaScript files.

### Dependencies

- Added `pwdlib[bcrypt]` — password hashing with bcrypt.

---

## [0.3.0] — 2026-03-28

### Added

- **Central gateway management** — Shoreguard transforms from a local sidecar
  into a central management plane for multiple remote OpenShell gateways (like
  Rancher for Kubernetes clusters). Gateways are deployed independently and
  registered with Shoreguard via API.
- **SQLAlchemy ORM + Alembic** — persistent gateway registry backed by
  SQLAlchemy with automatic embedded migrations on startup. SQLite by default,
  PostgreSQL via `SHOREGUARD_DATABASE_URL` for container deployments.
- **Gateway registration API** — `POST /api/gateway/register` to register
  remote gateways with endpoint, auth mode, and mTLS certificates.
  `DELETE /api/gateway/{name}` to unregister. `POST /{name}/test-connection`
  to explicitly test connectivity.
- **`ShoreGuardClient.from_credentials()`** — new factory method that accepts
  raw certificate bytes from the database instead of filesystem paths.
- **Background health monitor** — probes all registered gateways every 30
  seconds and updates health status (`last_seen`, `last_status`) in the
  registry.
- **`import-gateways` CLI command** — imports gateways from openshell filesystem
  config (`~/.config/openshell/gateways/`) into the database, including mTLS
  certificates. Replaces the old `migrate-v2` command.
- **`SHOREGUARD_DATABASE_URL`** — environment variable to configure an external
  database (PostgreSQL) for container/multi-instance deployments.
- **`--local` / `SHOREGUARD_LOCAL_MODE`** — opt-in flag to enable local Docker
  container lifecycle management (start/stop/restart/create/destroy). In local
  mode, filesystem gateways are auto-imported into the database on startup.
- **`--database-url` / `SHOREGUARD_DATABASE_URL`** — all env vars now also
  available as CLI flags.

### Changed

- **GatewayService refactored** — reduced from ~800 to ~250 lines. Gateway
  discovery now queries the SQLAlchemy registry instead of scanning the
  filesystem. Connection management (backoff, health checks) preserved.
- **Docker/CLI methods extracted** to `LocalGatewayManager`
  (`shoreguard/services/local_gateway.py`), only active in local mode.
- **Frontend updated** — "Create Gateway" replaced with "Register Gateway"
  modal (endpoint, auth mode, PEM certificate upload). Start/Stop/Restart
  buttons replaced with "Test Connection". "Destroy" renamed to "Unregister".
  New "Last Seen" column, Port column removed.
- **API route changes** — `POST /create` (202 LRO) → `POST /register` (201
  sync). `POST /{name}/destroy` → `DELETE /{name}`. Local lifecycle routes
  (start/stop/restart/diagnostics) return 404 unless `SHOREGUARD_LOCAL_MODE=1`.
- **Request-level logging** — gateway register, unregister, test-connection,
  and select routes now log at INFO/WARNING level. `LocalGatewayManager` logs
  Docker daemon errors, port conflicts, missing openshell CLI, and openshell
  command failures.
- **`api/main.py` split into modules** — extracted `cli.py` (Typer CLI +
  import logic), `pages.py` (HTML routes + auth endpoints), `websocket.py`
  (WebSocket handler), and `errors.py` (exception handlers). `main.py`
  reduced from 1 084 to ~190 lines (pure wiring).
- Version bumped to `0.3.0`.
- Test suite rewritten for registry-backed architecture (635 tests).
- **Logger names standardised** — all modules now use `getLogger(__name__)`
  instead of hardcoded `"shoreguard"`. Removes duplicate log lines caused
  by parent-logger propagation.
- **Unified log format** — single format (`HH:MM:SS LEVEL module message`)
  shared by shoreguard and uvicorn loggers with fixed-width aligned columns.
- Duplicate "API-key authentication enabled" log line removed.

### Fixed

- **SSRF protection** — `_is_private_ip()` now performs real DNS resolution
  instead of `AI_NUMERICHOST`. Hostnames that resolve to private/loopback/
  link-local addresses are correctly blocked. Includes a 2 s DNS timeout.
- **`import-gateways` crash on single gateway** — `registry.register()` failures
  no longer abort the entire import; individual errors are logged and
  skipped.
- **`from_active_cluster` error handling** — missing metadata files, corrupt
  JSON, and missing `gateway_endpoint` keys now raise
  `GatewayNotConnectedError` with a clear message instead of raw
  `FileNotFoundError` / `KeyError`.
- **`init_db()` failure logging** — database initialisation errors in the
  FastAPI lifespan are now logged before re-raising.
- **`_get_gateway_service()` guard** — raises `RuntimeError` if called before
  the app lifespan has initialised the service (instead of `AttributeError`
  on `None`).
- **WebSocket `RuntimeError` swallowed** — `RuntimeError` during
  `websocket.send_json()` is now debug-logged instead of silently passed.
- **SQLite pragma errors** — failures setting WAL/busy_timeout/synchronous
  pragmas are now logged as warnings.
- **`_import_filesystem_gateways` SSRF gap** — filesystem-imported gateways
  were not checked against `is_private_ip()`. Now blocked in non-local mode,
  consistent with the API registration endpoint.
- **`_import_filesystem_gateways` skipped count** — corrupt metadata JSON was
  logged but not counted in the `skipped` total, making the summary misleading.
- **`_import_filesystem_gateways` mTLS read error** — `read_bytes()` on cert
  files had no error handling (TOCTOU race). Now wrapped in try/except with
  a 64 KB size limit matching the API route.
- **`check_all_health` DB error isolation** — a database error updating health
  for one gateway no longer prevents health updates for all remaining gateways.
- **`select()` implicit name resolution** — `get_client()` was called without
  `name=`, relying on a filesystem round-trip via `active_gateway` file. Now
  passes the name explicitly.
- **CLI `import-gateways` NameError** — if `init_db()` failed, `engine` was
  undefined and `engine.dispose()` in the `finally` block raised `NameError`.
- **DB engine not disposed on shutdown** — the SQLAlchemy engine was not
  disposed during FastAPI lifespan shutdown, skipping the SQLite WAL
  checkpoint.
- **Docker start/stop errors silently swallowed** — `SubprocessError`/`OSError`
  in `_docker_start_container`/`_docker_stop_container` was caught but never
  logged.
- **Gateway start retry without summary** — when all 10 connection retries
  failed after a gateway start, no warning was logged.
- **Frontend 404 on gateway list page** — `inference-providers` was fetched
  without a gateway context, hitting a non-existent global route.

### Security

- SSRF DNS resolution bypass fixed (hostnames resolving to RFC 1918 / loopback
  addresses were not blocked).
- SSRF validation includes DNS timeout protection (2 s) to prevent slow-DNS
  attacks.
- **`remote_host` input validation** — `CreateGatewayRequest.remote_host` is
  now validated with a hostname regex (max 253 chars) before being passed to
  subprocess.
- **SSRF check skipped in local mode** — `is_private_ip()` checks at
  connect-time and import-time now allow private/loopback addresses when
  `SHOREGUARD_LOCAL_MODE` is set, since locally managed gateways always run
  on `127.0.0.1`.

### Dependencies

- Added `sqlalchemy >= 2.0` (runtime) — ORM and database abstraction.
- Added `alembic >= 1.15` (runtime) — embedded schema migrations on startup.

## [0.2.0] — 2026-03-27

### Added

- **API-key authentication** — optional shared API key via `--api-key` flag or
  `SHOREGUARD_API_KEY` env var. Supports Bearer tokens, HMAC-signed session
  cookies, and WebSocket query-param auth. Zero-config local development
  remains unchanged (auth is a no-op when no key is set).
- **Login page** for the web UI with session cookie management and automatic
  redirect for unauthenticated users.
- **Long-Running Operations (LRO)** — gateway and sandbox creation now return
  `202 Accepted` with an operation ID. Clients can poll `/api/operations/{id}`
  for progress. Includes automatic cleanup of expired operations.
- **`force` flag for gateway destroy** with dependency checking — prevents
  accidental deletion of gateways that still have running sandboxes unless
  `--force` is passed.
- **UNIMPLEMENTED error handling** — gRPC `UNIMPLEMENTED` errors now return a
  human-readable 501 response with feature context instead of a generic 500.
- OpenAPI documentation is automatically hidden when authentication is enabled.
- Session cookies set `secure` flag automatically when served over HTTPS.
- **`DEADLINE_EXCEEDED` mapping** — gRPC `DEADLINE_EXCEEDED` wird jetzt auf
  HTTP 504 (Gateway Timeout) gemappt.
- **`ValidationError` exception** — neuer Fehlertyp für Eingabevalidierung
  (ungültige Namen, shlex-Fehler) mit HTTP 400 Response.
- **Gateway/Sandbox name validation** — Regex-basierte Validierung von
  Ressourcennamen zur Verhinderung von Argument-Injection.
- **Client-IP tracking** — Client-IP wird bei Auth-Fehlern und
  Login-Fehlversuchen mitgeloggt.

### Changed

- Sandbox creation returns `202 Accepted` (was `201 Created`) to reflect
  the asynchronous LRO pattern.
- Destroyed gateways are now filtered from the gateway list by default.
- Version bumped to `0.2.0`.
- Exception-Handler im gesamten Codebase von breitem `except Exception` auf
  spezifische Typen (`grpc.RpcError`, `OSError`, `ssl.SSLError`,
  `ConnectionError`, `TimeoutError`) eingeschränkt.
- Logging deutlich erweitert: Debug-Logging für bisher stille Pass-Blöcke,
  Error-Level für Status ≥ 500, Warning-Level für Status < 500.
- WebSocket-Auth-Logging von INFO/WARNING auf DEBUG normalisiert.
- `friendly_grpc_error()` prüft jetzt freundliche Nachrichten vor Raw-Details.

### Fixed

- Auth credential check logic deduplicated into a single `check_request_auth()`
  helper shared by API dependencies, the `/api/auth/check` endpoint, and page
  auth guards.
- **Fire-and-forget Task-GC** — Background-Tasks werden jetzt in einem Set
  gehalten, um Garbage-Collection durch asyncio zu verhindern.
- **Cross-Thread WebSocket-Signaling** — `asyncio.Event` durch
  `threading.Event` ersetzt für korrekte Thread-übergreifende Signalisierung.
- **WebSocket Queue-Overflow** — `QueueFull`-Exception wird abgefangen mit
  Fallback auf `cancel_event`.
- **Event-Loop-Blocking** — `get_client()` im WebSocket-Handler mit
  `asyncio.to_thread()` gewrappt.
- **gRPC-Client-Leak** — Client-Leak in `_try_connect()` behoben, wenn
  Health-Check fehlschlägt.
- **Login-Redirect-Validation** — Open-Redirect-Schutz: URLs die nicht mit `/`
  beginnen oder mit `//` starten werden abgelehnt.
- **Error-Message-Sanitization** — `friendly_grpc_error()` verhindert, dass
  rohe gRPC-Fehlermeldungen an API-Clients geleitet werden.
- **Thread-Safety** — `threading.Lock` für `GatewayService._clients` und
  thread-safe Reads in `OperationStore.to_dict()`.
- **YAML-Parsing-Robustheit** — `YAMLError`, None- und Skalar-Werte werden in
  `presets.py` abgefangen.
- **Metadata-Datei-Robustheit** — `JSONDecodeError` und `OSError` bei
  Gateway-Metadata-Reads mit Fallback behandelt.

### Security

- Open-Redirect-Schutz auf der Login-Seite.
- API-Fehlermeldungen werden sanitisiert, um interne Details nicht preiszugeben.
- Thread-sichere Client-Verwaltung und Operation-Store-Zugriffe.
- Argument-Injection-Prävention durch Regex-Namensvalidierung.
- Client-IP-Logging bei Auth-Events für Security-Monitoring.

## [0.1.0] — 2026-03-25

Initial release.

### Added

- **Sandbox management** — create, list, get, delete sandboxes with custom
  images, environment variables, GPU support, and provider integrations.
- **Real-time monitoring** — WebSocket streaming of sandbox logs, events, and
  status changes.
- **Command execution** — run commands inside sandboxes with stdout/stderr
  capture.
- **SSH sessions** — create and revoke interactive SSH terminal sessions.
- **Security policy editor** — visual network rule, filesystem access, and
  process/Landlock policy management without raw YAML editing.
- **Policy approval workflow** — review, approve, reject, or edit agent-
  requested endpoint rules with real-time WebSocket notifications.
- **Policy presets** — 9 bundled templates (PyPI, npm, Docker Hub, NVIDIA NGC,
  HuggingFace, Slack, Discord, Telegram, Jira, Microsoft Outlook).
- **Multi-gateway support** — manage multiple OpenShell gateways with status
  monitoring, diagnostics, and automatic reconnection.
- **Provider management** — CRUD for inference/API providers with credential
  templates and community sandbox browser.
- **Sandbox wizard** — guided step-by-step sandbox creation with agent type
  selection and one-click preset application.
- **Web dashboard** — responsive Bootstrap 5 UI with gateway, sandbox, policy,
  approval, log, and terminal views.
- **REST API** — full async FastAPI backend with Swagger UI documentation.
- **CLI** — `shoreguard` command with configurable host, port, log level, and
  auto-reload.
