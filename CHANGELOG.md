# Changelog

All notable changes to Shoreguard are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased] — v0.2.0

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
