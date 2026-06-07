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

Python 3.14, managed with `uv`. Entry point is `shoreguard.api.main:cli`.

## Commands

```bash
uv sync --group dev                       # install deps (dev group required for tests/lint)

just dev                                  # run dev server: shoreguard --local --no-auth (http://localhost:8888)
just test                                 # unit tests (pytest -m 'not integration')
just check                                # ruff check + ruff format --check + pyright + unit tests
just lint / just format                   # ruff check . / ruff format .

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
single OpenShell channel and exposes submanagers (`SandboxManager`, `PolicyManager`,
`ProviderManager`, `ProviderProfileManager`, `ApprovalManager`). It is deliberately
**synchronous** — service layers wrap calls in `asyncio.to_thread` when called from
async routes. Generated proto stubs live in [shoreguard/client/_proto/](shoreguard/client/_proto/);
**do not hand-edit them** — regenerate with `scripts/generate_proto.py`. The `_proto`
tree is excluded from ruff, pyright, coverage, and mutmut.

**Service singletons:** services in [shoreguard/services/](shoreguard/services/) are
module-global singletons (`gateway_service`, `audit_service`, `operation_service`,
`webhook_service`, …) initialized in the FastAPI **lifespan** in
[main.py](shoreguard/api/main.py) and torn down / re-created per test in
[tests/conftest.py](tests/conftest.py). When adding a service, follow this pattern and
wire it into both lifespan and the conftest fixture, or it will be `None` in tests.

**Database** ([db.py](shoreguard/db.py)): SQLAlchemy 2.0. Defaults to SQLite (WAL mode,
0600 perms); PostgreSQL for production and the integration/postgres suites. `init_db()`
runs the **Alembic migrations embedded in the package** on startup (versions in
[shoreguard/alembic/versions/](shoreguard/alembic/versions/)). There are **two engines**: a
sync engine for most services and a separate **async** engine for `AsyncOperationService`
(long-running operations). The ShoreGuard DB only holds management-plane state (gateway
registry, audit log, operations/LROs, approval workflows, policy pins, SBOM, boot hooks,
sandbox metadata, users); the sandboxes and policies themselves live on the OpenShell
gateways. All models are in the single file [models.py](shoreguard/models.py).

**Long-running operations (LRO):** sandbox create and similar slow gateway calls return an
operation id and run as supervised async tasks — [api/lro.py](shoreguard/api/lro.py) +
`AsyncOperationService` in [services/operations.py](shoreguard/services/operations.py).

**Settings** ([settings.py](shoreguard/settings.py)): many `pydantic-settings` `BaseSettings`
classes, each with its own `SHOREGUARD_*` / `SHOREGUARD_<AREA>_` env prefix, aggregated into
one `Settings`. Access via the `get_settings()` singleton; `reset_settings()` clears it (tests
do this so `monkeypatch.setenv` takes effect). `enforce_production_safety()` runs at startup.

**Auth** ([api/auth.py](shoreguard/api/auth.py), [api/oidc.py](shoreguard/api/oidc.py)):
session + JWT, RBAC roles Admin/Operator/Viewer with gateway-scoped overrides, optional OIDC.
Routes guard with `require_auth` / `require_role`. `--no-auth` sets a global bypass for dev.

**Frontend:** server-rendered Jinja2 templates in [frontend/templates/](frontend/templates/)
served by [api/pages.py](shoreguard/api/pages.py), with Alpine.js + vanilla JS in
[frontend/js/](frontend/js/). At build time hatch force-includes `frontend/` into the wheel as
`shoreguard/_frontend`; it is mounted at `/static`.

## Conventions & gates

- **Docstrings are mandatory and linted.** Google style, enforced by both ruff (`D` rules)
  and `pydoclint` — public functions need `Args:` / `Returns:` / `Raises:` sections that match
  the signature. Tests and `_proto/` are exempt. This bites often; check `pydoclint` before pushing.
- **Surface-coverage invariant:** every upstream OpenShell RPC ShoreGuard covers must be reachable
  through a client method, a REST route, **and** a UI `apiFetch` call. `scripts/check_coverage.py`
  enforces this as a required CI job — adding an RPC/route without all three layers fails CI. Use the
  explicit allowlist in that script for intentionally-unconsumed (supervisor-side) RPCs.
- Ruff line length 100, target py314, rules `E,F,I,UP,D`. Pyright standard mode over `shoreguard` + `tests`.
- **Conventional Commits** with a subsystem scope: `feat(api): …`, `fix(policy): …` (`api`, `ui`,
  `policy`, `sandbox`, `tf`, `alembic`, `docs`, `ci`, …). Update `CHANGELOG.md` in the same PR.
- DB migrations need a documented rollback path (or an explicit `NotImplementedError` with a comment);
  `scripts/verify_migrations.sh` must pass.
- Fix bugs at the source: the Terraform provider and OpenClaw/Paperclip plugins are first-party repos —
  don't work around their bugs downstream.
