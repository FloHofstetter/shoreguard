# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ShoreGuard is an open-source **control plane for NVIDIA OpenShell**. It is a
single Python process that bundles a FastAPI REST API, a server-rendered web
UI, and a Typer CLI. One ShoreGuard instance manages **multiple OpenShell
gateways** (dev/staging/prod), each addressed by name. ShoreGuard never holds
agent workloads itself — it talks to gateways over **gRPC + mTLS**, and the
gateways run the actual sandboxes. Provider API keys live in ShoreGuard and are
injected by OpenShell's L7 proxy; agents only ever see `inference.local/v1`.

Python 3.14, managed with `uv`. Entry point is `shoreguard.cli:cli`; the ASGI
app for uvicorn is `shoreguard.api.main:app` (a thin shim over
`shoreguard.app:create_app`).

## Commands

```bash
uv sync --group dev                       # install deps (dev group required for tests/lint)

just dev                                  # build islands bundle + run dev server: shoreguard --local --no-auth (http://localhost:8888)
just test                                 # unit tests (pytest -m 'not integration')
just check                                # frontend typecheck/tests + ruff + pyright + unit tests
just lint / just format                   # ruff check . / ruff format .
just frontend-install                     # npm install in frontend/
just frontend-build / frontend-watch      # Vite build of the Preact islands into frontend/dist
just frontend-check                       # tsc --noEmit + vitest

uv run pytest tests/test_foo.py::test_bar # run a single test
uv run pytest -n auto -m 'not integration'# parallel via pytest-xdist
uv run pytest -m integration              # needs Postgres + a live OpenShell gateway (see below)
uv run pytest -m postgres                 # tests requiring a live PostgreSQL

uv run pyright                            # type-check (standard mode)
uv run pydoclint shoreguard               # docstring lint — Google style, enforced (see Conventions)
bash scripts/verify_migrations.sh         # Alembic round-trip on a throwaway DB
uv run python scripts/check_coverage.py   # surface-coverage gate (see Surface coverage invariant)
uv run bandit -r shoreguard -ll -q        # SAST
uv run pip-audit                          # dependency CVEs

uv run python scripts/generate_proto.py   # regenerate gRPC stubs from upstream OpenShell .proto
```

`uv run pre-commit install --hook-type pre-commit --hook-type pre-push` arms the
local gates (ruff, bandit, detect-secrets against `.secrets.baseline`, etc.).

**Integration tests** auto-resolve a gateway in this order: `SHOREGUARD_TEST_ENDPOINT`
env var → `OPENSHELL_GATEWAY` env var → auto-start a foreground `openshell-gateway`
daemon (if the binary is on PATH) → otherwise skip. So `pytest -m integration`
silently skips when no gateway is reachable rather than failing.

## Architecture

**Request flow:** operator (Web UI / REST / Terraform) → ShoreGuard FastAPI →
`ShoreGuardClient` (gRPC+mTLS) → OpenShell gateway → sandbox. All REST routes for
gateway-scoped resources are mounted under `/api/gateways/{gw}/...`
([main.py](shoreguard/api/main.py) builds the `gw_api` router). The `resolve_gateway`
dependency parses `{gw}` and stashes it on `request.state`; `get_client` then resolves
the live gRPC client for *that* gateway. This per-gateway routing is the core of the
multi-gateway design — see [deps.py](shoreguard/api/deps.py).

**Two-layer gateway model** — keep these distinct:
- [services/registry.py](shoreguard/services/registry.py) owns *what gateways exist*
  (DB-backed CRUD: name, endpoint, mTLS creds).
- [services/gateway.py](shoreguard/services/gateway.py) owns *the live connections* —
  an in-memory cache of `ShoreGuardClient` instances with per-gateway health probing
  and connection backoff, so a CRUD edit never tears down an in-flight call and one
  dead channel doesn't wedge the service.

**gRPC client** ([shoreguard/client/](shoreguard/client/)): `ShoreGuardClient` wraps a
single OpenShell **grpc.aio** channel and exposes submanagers (`SandboxManager`,
`PolicyManager`, `ProviderManager`, `ProviderProfileManager`, `ApprovalManager`,
`ServiceManager`). Every RPC is **async**; server streams are async iterators and the
bidi exec/forward streams take an `asyncio.Queue` request feed. Retry/backoff lives in
[client/_resilience.py](shoreguard/client/_resilience.py) (async). Synchronous callers
(the Typer CLI) wrap calls in `asyncio.run`. Generated proto stubs live in
[shoreguard/client/_proto/](shoreguard/client/_proto/); **do not hand-edit them** —
regenerate with `scripts/generate_proto.py`. The `_proto` tree is excluded from ruff,
pyright, coverage, and mutmut.

**Composition root:** all application services are constructed once per process by
`build_container()` in [container.py](shoreguard/container.py), which returns a
`ServiceContainer` dataclass. The lifespan in [app.py](shoreguard/app.py) installs it
(`install()` / `try_get_container()`); routes resolve services via
`get_services()` in [api/deps.py](shoreguard/api/deps.py). Tests build their container
through the **same** `build_container()` code path (autouse `container` fixture in
[tests/conftest.py](tests/conftest.py)) — adding a field to the container wires it
everywhere at once. Per-request wrappers (`SandboxService`, `PolicyService`, …) are
constructed in route dependencies around the gateway-bound client and are async-native.

**Background tasks:** the periodic loops (cleanup, gateway health, discovery,
drift detection, cert rotation, usage metering, daily digest) are declarative
`PeriodicTask` specs in
[tasks/definitions.py](shoreguard/tasks/definitions.py), driven by the generic
`TaskSupervisor` ([tasks/supervisor.py](shoreguard/tasks/supervisor.py)) with failure
backoff and a health snapshot consumed by `/readyz`. Disabled features register no task.
The gateway health loop fires `gateway.unreachable`/`gateway.recovered` webhook events
on status transitions.

**Database** ([shoreguard/db/](shoreguard/db/)): SQLAlchemy 2.0. Defaults to SQLite (WAL
mode, 0600 perms); PostgreSQL for production and the integration/postgres suites.
`init_db()` runs the **Alembic migrations embedded in the package** on startup. The
pre-v0.38 17-step chain is squashed into a single `v2_baseline` revision generated from
the models; v0.37 databases are stamped automatically, older ones must pass through
v0.37 first. Because the baseline does `create_all` from the live models, migrations
ON TOP of it (101+, e.g. kill switch, budgets) must guard `create_table` with an
existence check — fresh DBs already have the tables. The **async engine**
(`init_async_db()` + `get_async_session_factory()`) backs every service — including
auth — with `AsyncSession`; a sync engine exists only to run Alembic at startup.
The ShoreGuard DB only holds management-plane state (gateway registry, audit log,
operations/LROs, approval workflows, policy pins, SBOM, boot hooks, sandbox metadata,
users); the sandboxes and policies themselves live on the OpenShell gateways. Models are
split per domain in [shoreguard/db/models/](shoreguard/db/models/) and re-exported from
`shoreguard.models` for compatibility (Alembic env included).

**Long-running operations (LRO):** sandbox create and similar slow gateway calls return an
operation id and run as supervised async tasks — [api/lro.py](shoreguard/api/lro.py) +
`AsyncOperationService` in [services/operations.py](shoreguard/services/operations.py).

**Settings** ([settings.py](shoreguard/settings.py)): many `pydantic-settings` `BaseSettings`
classes, each with its own `SHOREGUARD_*` / `SHOREGUARD_<AREA>_` env prefix, aggregated into
one `Settings`. Access via the `get_settings()` singleton; `reset_settings()` clears it (tests
do this so `monkeypatch.setenv` takes effect). `enforce_production_safety()` runs at startup.

**Auth** ([api/auth/](shoreguard/api/auth/), [api/oidc.py](shoreguard/api/oidc.py)): a
package — `core` (passwords, sessions, lockout, shared `AuthState`), `rbac` (FastAPI
dependencies), `users`, `service_principals`, `gateway_roles`, `groups`; everything
public re-exported from the package `__init__`. All DB-touching auth functions are
**async** (the CLI wraps them in `asyncio.run`). Session + JWT, RBAC roles
Admin/Operator/Viewer with gateway-scoped overrides, optional OIDC. Routes guard with
`require_auth` / `require_role`. `--no-auth` (or `set_no_auth(True)` in tests) enables
the dev bypass. The auth REST endpoints live in
[api/routes/auth.py](shoreguard/api/routes/auth.py) and
[api/routes/users.py](shoreguard/api/routes/users.py);
[api/pages.py](shoreguard/api/pages.py) only renders HTML.

**Frontend:** server-rendered Jinja2 shells in [frontend/templates/](frontend/templates/)
served by [api/pages.py](shoreguard/api/pages.py); all interactivity is **Preact +
TypeScript islands** ([frontend/src/](frontend/src/), built with Vite into
`frontend/dist` — there is no Alpine.js or vanilla-JS bundle anymore). A page mounts a
component via `<div data-island="…" data-props='…'>`; `src/main.ts` lazy-loads the
code-split chunk, and `src/shell/` provides the app chrome (gateway switcher, command
palette, theme, health, shortcuts). Shared libs live in `src/lib/` (`api.ts` apiFetch,
`notify.tsx` toasts/confirm, `sandbox-ws.ts` live events). Build with
`just frontend-build` (or `frontend-watch` during dev); gates are `tsc --noEmit` +
vitest (`just frontend-check`). `scripts/generate_api_types.py` regenerates
`src/api/types.gen.ts` from the OpenAPI schema. Hatch ships `frontend/dist` plus
css/templates/vendor into the wheel as `shoreguard/_frontend`, mounted at `/static`.

## Conventions & gates

- **Docstrings are mandatory and linted.** Google style, enforced by both ruff (`D` rules)
  and `pydoclint` — public functions need `Args:` / `Returns:` / `Raises:` sections that match
  the signature. Tests and `_proto/` are exempt. This bites often; check `pydoclint` before pushing.
- **Surface-coverage invariant:** every upstream OpenShell RPC ShoreGuard covers must be reachable
  through a client method, a REST route, **and** a UI `apiFetch` call. `scripts/check_coverage.py`
  enforces this as a required CI job — adding an RPC/route without all three layers fails CI. It
  scans `frontend/src/**/*.{ts,tsx}` for `apiFetch(`…`)` template literals (use the `API` constant
  as the URL prefix so normalization works). Use the explicit allowlist in that script for
  intentionally-unconsumed (supervisor-side) RPCs.
- Ruff line length 100, target py314, rules `E,F,I,UP,D`. Pyright standard mode over `shoreguard` + `tests`.
- **Conventional Commits** with a subsystem scope: `feat(api): …`, `fix(policy): …` (`api`, `ui`,
  `policy`, `sandbox`, `tf`, `alembic`, `docs`, `ci`, …). Update `CHANGELOG.md` in the same PR.
- DB migrations need a documented rollback path (or an explicit `NotImplementedError` with a comment);
  `scripts/verify_migrations.sh` must pass.
- Fix bugs at the source: the Terraform provider and OpenClaw/Paperclip plugins are first-party repos —
  don't work around their bugs downstream.
