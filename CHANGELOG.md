# Changelog

All notable changes to Shoreguard are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.15.0] — 2026-04-04

### Added

- **Gateway description** — free-text `description` field on gateways for
  documenting purpose and context (e.g. "Production EU-West for ML team").
- **Gateway labels** — key-value labels (`env=prod`, `team=ml`, `region=eu-west`)
  stored as `labels_json` column. Kubernetes-style key validation, max 20 labels
  per gateway, values up to 253 chars.
- **`PATCH /api/gateway/{name}`** — new endpoint to update gateway description
  and/or labels after registration (admin only). Supports partial updates via
  Pydantic `model_fields_set`.
- **Label filtering** — `GET /api/gateway/list?label=env:prod&label=team:ml`
  filters gateways by labels (AND semantics).
- **Alembic migration 004** — adds `description` (Text) and `labels_json` (Text)
  columns to the `gateways` table.

### Changed

- **Gateway list UI** — new description column (hidden on small screens) and
  label badges displayed below gateway names.
- **Gateway detail UI** — description and labels shown in details card with
  inline edit form (admin only).
- **Gateway registration modal** — new description textarea and labels textarea
  (one `key=value` per line).
- **`GatewayRegistry`** — `register()`, `_to_dict()`, and `list_all()` extended
  for description, labels, and label filtering. New `update_gateway_metadata()`
  method with sentinel-based partial updates.

## [0.14.0] — 2026-04-04

### Added

- **Notification channels** — webhooks now support `channel_type` field with
  values `generic` (default, HMAC-signed), `slack` (Block Kit formatting),
  `discord` (embed formatting), and `email` (SMTP delivery). Alembic migration
  003 adds `channel_type` and `extra_config` columns to the `webhooks` table.
- **Message formatters** — `shoreguard/services/formatters.py` with
  channel-specific formatting: Slack Block Kit with mrkdwn and color coding,
  Discord embeds with color-coded fields, plain-text email bodies.
- **Prometheus `/metrics` endpoint** — unauthenticated, exposes
  `shoreguard_info`, `shoreguard_gateways_total` (by status),
  `shoreguard_operations_total` (by status),
  `shoreguard_webhook_deliveries_total` (success/failed),
  and `shoreguard_http_requests_total` (by method and status code).
- **HTTP request counting middleware** — counts all API requests by method
  and status code for Prometheus.
- **`OperationStore.status_counts()`** — thread-safe method returning
  operation counts grouped by status.

### Changed

- **`WebhookService`** refactored for channel-type-aware delivery: `_deliver`
  dispatches to `_deliver_http` (generic/slack/discord) or `_deliver_email`.
  HMAC signature only applied for `generic` channel type.
- **Webhook API routes** accept `channel_type` and `extra_config` in create
  and update requests. Email channel requires `smtp_host` and `to_addrs`
  in `extra_config`.
- **Webhook API docs expanded** — channel types table, email `extra_config`
  example, corrected event types, Prometheus metrics table with scrape config.
- **Deployment docs** — new monitoring section with Prometheus scrape config.
- **README** — notifications and Prometheus metrics in features list and roadmap.
- Version bumped to `0.14.0`.
- 791 tests (up from 770).

### Fixed

- **`deps.py` type safety** — `get_client()`, `set_client()`, and
  `reset_backoff()` now raise `HTTPException(500)` when called without a
  gateway context instead of passing `None` to the gateway service. Fixes
  3 pre-existing pyright `reportArgumentType` errors.

### Dependencies

- Added `prometheus_client>=0.21`.
- Added `aiosmtplib>=3.0`.

## [0.13.0] — 2026-04-04

### Added

- **Docker deployment polish** — OCI image labels in `Dockerfile`, restart
  policies, dedicated `shoreguard-net` bridge network, configurable port and
  log level, and resource limits in `docker-compose.yml`.
- **`.env.example`** — documented all environment variables with required/optional
  separation for quick Docker Compose setup.
- **`docker-compose.dev.yml`** — standalone development compose with SQLite,
  hot-reload, no-auth, and local gateway mode. No PostgreSQL required.
- **Justfile** — task runner with `dev`, `test`, `lint`, `format`, `check`,
  `docker-build`, `docker-up`, `docker-down`, `docs`, and `sync` targets.
- **Webhooks** — event subscriptions with HMAC-SHA256 signing, Alembic
  migration 002, `WebhookService` with async delivery, and admin API
  (`POST/GET/DELETE /api/webhooks`).

### Changed

- **README overhaul** — new "Why ShoreGuard?" section, dual quick-start paths
  (pip + Docker Compose), collapsible screenshot gallery, expanded development
  section with Justfile references, updated roadmap.
- **Deployment docs expanded** — step-by-step Docker setup, full environment
  variable reference table, backup/restore procedures, network isolation
  explanation, upgrade process, and troubleshooting section.
- **Contributing docs expanded** — "Clone to first sandbox" walkthrough,
  Justfile task runner section, corrected clone URL and port references.
- **Local mode docs expanded** — developer workflow section with `--no-auth`
  combination, SQLite defaults, and state reset instructions.
- **mkdocs nav** — added migration runbook to admin guide navigation.
- Version bumped to `0.13.0`.

### Fixed

- **Duplicate auth log** — removed redundant "Authentication DISABLED" warning
  from `init_auth()` that appeared unformatted when running with `--reload`.
- **Logger name formatting** — replaced one-shot name rewriting with a custom
  `Formatter` that strips the `shoreguard.` prefix at render time, so
  late-created loggers (e.g. `shoreguard.db`) are also shortened correctly.
- **Contributing docs** — corrected clone URL (`your-org` → `FloHofstetter`)
  and port reference (`8000` → `8888`).

## [0.12.0] — 2026-04-03

### Added

- **Inference timeout** — `timeout_secs` field on `PUT /api/gateways/{gw}/inference`
  allows configuring per-route request timeouts (0 = default 60s). Displayed in the
  gateway detail inference card.
- **L7 query parameter matchers** — network policy rules can now match on URL query
  parameters using `glob` (single pattern) or `any` (list of patterns) matchers.

### Changed

- **Protobuf stubs regenerated** from OpenShell v0.0.22 (was ~v0.0.16).

## [0.11.0] — 2026-04-03

### Added

- **Docker containerisation** — multi-stage `Dockerfile` and
  `docker-compose.yml` (ShoreGuard + PostgreSQL) for production deployments.
- **Health probes** — unauthenticated `GET /healthz` (liveness) and
  `GET /readyz` (readiness — checks database and gateway service).
- **`protobuf` runtime dependency** — added to `pyproject.toml` (was
  previously only available transitively via `grpcio-tools` in dev).
- `.dockerignore` for minimal build context.

### Fixed

- **PostgreSQL migration** — `users.is_active` column used
  `server_default=sa.text("1")` which fails on PostgreSQL. Changed to
  `sa.true()` for cross-database compatibility.
- **Gateway health endpoint** — `GET /api/gateways/{gw}/health` called
  `get_client()` directly instead of via dependency injection, causing
  `GatewayNotConnectedError` to return 200 instead of 503.

### Changed

- FastAPI `version` field now matches the package version (was stale at
  `0.8.0`).

## [0.10.0] — 2026-04-03

### Removed

- **"Active gateway" concept** — the server-side `active_gateway` file
  (`~/.config/openshell/active_gateway`) is no longer read or written by the
  web service. Every gateway operation now requires an explicit gateway name
  from the URL. Removed endpoints: `POST /{name}/select`, `GET /info`,
  `POST /start`, `POST /stop`, `POST /restart` (non-named variants). The
  named variants (`/{name}/start` etc.) remain unchanged.
- **`active` field** removed from all gateway API responses (`list`, `info`,
  `register`).
- Service methods removed: `get_active_name()`, `write_active_gateway()`,
  `select()`, `health()`.
- Auto-select of first registered gateway removed from `register()`.

### Changed

- **Stateless gateway routing** — the `name` parameter is now required on
  `get_client()`, `set_client()`, `reset_backoff()`, `get_info()`, and
  `get_config()`. No method falls back to the active gateway file anymore.
- **`GET /info` → `GET /{name}/info`** — gateway info endpoint is now
  name-scoped.
- **`GET /config` → `GET /{name}/config`** — gateway config endpoint is now
  name-scoped.
- **`LocalGatewayManager`** — `start()`, `stop()`, `restart()` now require
  a gateway name. Connection and client management simplified: always
  operates on the explicitly named gateway.
- **Frontend inference config** — now shows when gateway is connected
  (`gw.connected`) instead of when it was the "active" gateway
  (`gw.active`). Gateway list highlights connected gateways.
- **Health store** — uses `GW` directly for gateway name instead of
  fetching from `/api/gateway/info`.
- Version bumped to `0.10.0`.
- 756 tests (down from 774 — 18 tests for removed active-gateway
  functionality deleted).

---

## [0.9.0] — 2026-04-03

### Added

- **Sidebar navigation** — collapsible sidebar with grouped navigation
  (Gateways, Policies, gateway-scoped Sandboxes/Providers, admin-only
  Audit/Users). Replaces the icon buttons in the topbar. Responsive:
  collapses to hamburger menu on mobile (<768px).
- **Light/dark theme toggle** — switchable via sidebar button, persisted
  in `localStorage`. All custom CSS variables scoped to
  `[data-bs-theme]`; Bootstrap 5.3 handles the rest automatically.

### Fixed

- **Audit page breadcrumbs** — audit.html now has breadcrumbs and uses
  the standard layout instead of `container-fluid`.
- **Dashboard breadcrumbs** — dashboard.html now has breadcrumbs.
- **Theme-aware tables** — removed hardcoded `table-dark` class from all
  templates and JS files; tables now adapt to the active theme.

## [0.8.0] — 2026-04-03

### Fixed

- **RBAC response_model crash** — added `response_model=None` to 17 route
  decorators (16 in `pages.py`, 1 in `main.py`) returning `TemplateResponse`,
  `HTMLResponse`, or `RedirectResponse`. Prevents FastAPI Pydantic serialization
  errors on non-JSON responses.
- **IntegrityError/ValueError split** — gateway-role SET endpoints now return
  409 on constraint conflicts and 404 on missing user/SP/gateway, instead of a
  blanket 404 for both.

### Added

- **Migration verification tests** — 5 tests (`tests/test_migrations.py`)
  covering SQLite and PostgreSQL: fresh-DB, head revision, schema-matches-models,
  downgrade, and PostgreSQL fresh-DB.
- **RBAC regression & validation tests** — 10 new tests (`tests/test_rbac.py`)
  for DELETE gateway-role 404s, invalid gateway name 400s, and invalid role 400s
  (user and SP symmetry).
- **Migration check script** — `scripts/verify_migrations.sh` runs all Alembic
  migrations against a fresh database and verifies the final revision.
- **Migration CI workflow** — `.github/workflows/test-migrations.yml` runs
  migration tests on SQLite and PostgreSQL for PRs touching migrations or models.
- **PR template** — `.github/PULL_REQUEST_TEMPLATE.md` with migration checklist.
- **Migration runbook** — `docs/admin/migration-runbook.md` with backup,
  upgrade, and rollback procedures.
- **Warning logs on error paths** — all gateway-role endpoints now log
  `logger.warning()` for invalid names, invalid roles, not-found, and conflict
  responses.
- **Backoff for background tasks** — `_cleanup_operations()` and
  `_health_monitor()` double their interval (up to a cap) after 10 consecutive
  failures and reset on success.
- `postgres` pytest marker in `pyproject.toml`.

### Security

- **Shell injection fix** — `verify_migrations.sh` passes database URL via
  `os.environ` instead of bash interpolation in a Python heredoc.

### Changed

- **Migrations squashed** — all 7 incremental migrations replaced by a single
  `001_initial_schema.py` that creates the final schema directly. Existing
  databases must be reset (`rm ~/.config/shoreguard/shoreguard.db`).
- Migration CI caches `uv` dependencies via `enable-cache: true`.

---

## [0.7.1] — 2026-04-01

### Added

- **API reference docs** — mkdocstrings[python] generates reference pages from
  existing Google-style docstrings. New pages under `docs/reference/`: Client,
  Services, API Internals, Models, and Config & Exceptions.

## [0.7.0] — 2026-04-01

### Added

- **pydoclint integration** — new `[tool.pydoclint]` section in `pyproject.toml`
  with maximum strictness (Google-style, `skip-checking-short-docstrings = false`,
  all checks enabled). Added `pydoclint >= 0.8` as dev dependency.
- **Comprehensive Google-style docstrings** — all 1 193 pydoclint violations
  resolved across the entire codebase. Every function, method, and class now
  has `Args:`, `Returns:`, `Raises:`, and `Yields:` sections as appropriate.
  Compatible with mkdocstrings for future API reference generation.
- **Page templates** — dedicated HTML templates for approval edit, approval
  history, gateway register, gateway roles, policy revisions, and provider
  form pages, replacing Bootstrap modal dialogs.

### Changed

- **Database schema cleanup (migration 007):**
  - Timestamp columns (`registered_at`, `last_seen`, `created_at`, `last_used`,
    `timestamp`) converted from `String` to `DateTime(timezone=True)` across
    `gateways`, `users`, `service_principals`, and `audit_log` tables.
  - `gateways` table rebuilt with auto-incrementing integer primary key (`id`)
    replacing the old `name`-based primary key.
  - `user_gateway_roles` and `sp_gateway_roles` migrated from `gateway_name`
    (String FK) to `gateway_id` (Integer FK) with `ON DELETE CASCADE`.
  - `audit_log` column `gateway` renamed to `gateway_name`; new `gateway_id`
    FK added with `ON DELETE SET NULL`.
- **Audit service refactored** — uses `with session_factory()` context manager
  instead of manual `session.close()` in finally blocks. Gateway ID resolution
  via FK lookup on write.
- Version bumped to `0.7.0`.

### Fixed

- **`GatewayNotConnectedError` in `_try_connect_from_config`** — exception is
  now caught instead of propagating as an unhandled error.
- **`request.state.role` not set from `_require_page_auth`** — page auth
  guard now correctly stores the resolved role in request state.

---

## [0.6.0] — 2026-03-31

### Added

- **Gateway-scoped RBAC** — per-gateway role overrides for users and service
  principals. Alembic migration 006 adds `user_gateway_roles` and
  `sp_gateway_roles` tables.
- **Policy diff viewer** — compare two policy revisions side-by-side.
- **Hardened RBAC** — async correctness improvements and additional test coverage.

---

## [0.5.0] — 2026-03-30

### Added

- **Persistent audit log** — all state-changing operations (sandbox/policy/gateway
  CRUD, user management, approvals, provider changes) are recorded in a database
  table with actor, role, action, resource, gateway context, and client IP.
- **Audit API** — `GET /api/audit` lists entries with filters (actor, action,
  resource type, date range). `GET /api/audit/export?format=csv|json` exports
  the full log. Both endpoints are admin-only.
- **Audit page** — `/audit` admin page with filter inputs, pagination, and
  CSV/JSON export buttons. Built with Alpine.js.
- **Alembic migration 005** — `audit_log` table with indexes on timestamp,
  actor, action, and resource type.
- **Audit cleanup** — entries older than 90 days are automatically purged by
  the existing background cleanup task.

### Fixed

- **Fail-closed auth** — when the database is unavailable, requests are now
  denied with 503 instead of silently granting admin access.
- **Async audit logging** — `audit_log()` is now async and runs DB writes in a
  thread pool via `asyncio.to_thread`, preventing event-loop blocking on every
  state-changing request.
- **UnboundLocalError in AuditService** — `log()`, `list()`, and `cleanup()` no
  longer crash if the session factory itself raises; session is now guarded with
  `None` checks in except/finally blocks.
- **Audit actor for auth events** — login, setup, register, and invite-accept
  now set `request.state.user_id` before calling `audit_log()`, so the audit
  trail records the actual user instead of "unknown".
- **Failed login auditing** — failed login attempts now produce a
  `user.login_failed` audit entry, enabling brute-force detection.
- **Authorization failure auditing** — `require_role()` now writes an
  `auth.forbidden` audit entry when a user is denied access.
- **Audit ordering in approvals** — all six approval endpoints now log the audit
  entry *after* the operation succeeds, preventing false entries on failure.
- **Conditional delete audit** — `sandbox.delete` and `provider.delete` only
  write audit/log entries when the resource was actually deleted.
- **Async background cleanup** — the periodic cleanup task now uses
  `asyncio.to_thread` for DB calls instead of blocking the event loop.
- **Gateway retry button** — the "Retry" button in the gateway error banner now
  correctly calls `Alpine.store('health').check()` instead of the removed
  `checkGatewayHealth()` function.

### Changed

- **Frontend migrated to Alpine.js** — all 20+ pages rewritten from Vanilla JS
  template-literal rendering (`innerHTML = renderX(data)`) to Alpine.js reactive
  directives (`x-data`, `x-for`, `x-text`, `x-show`, `@click`). No build step
  required — Alpine.js loaded via CDN.
- **Three Alpine stores** replace scattered global state:
  - `auth` — role, email, authenticated status (replaces inline script + `window.SG_ROLE`)
  - `toasts` — notification queue (replaces `showToast()` DOM manipulation)
  - `health` — gateway connectivity monitoring (replaces `checkGatewayHealth()` globals)
- **XSS surface reduced** — Alpine's `x-text` auto-escapes all dynamic content,
  eliminating the need for manual `escapeHtml()` calls in templates.
- **Render functions removed** — `renderGatewayTable()`, `renderSandboxList()`,
  `renderDashboard()`, and ~50 other `renderX()` functions replaced by declarative
  Alpine templates in HTML.
- **`app.js` slimmed** — reduced from ~340 lines to ~95 lines. Only retains
  `apiFetch()`, `showConfirm()`, `escapeHtml()`, `formatTimestamp()`, `navigateTo()`,
  and URL helpers.
- **WebSocket integration** — sandbox detail, logs, and approvals pages receive
  live updates via `CustomEvent` dispatching from `websocket.js` to Alpine components.
- Version bumped to `0.5.0`.
- **717 tests** (up from 710), including audit service, API route, and DB schema tests.

---

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
