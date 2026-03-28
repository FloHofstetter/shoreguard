# Changelog

All notable changes to Shoreguard are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
- Version bumped to `0.3.0`.
- Test suite rewritten for registry-backed architecture (610 tests).
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
