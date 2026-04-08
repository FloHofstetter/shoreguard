# Code Structure

This page describes the high-level layout of the ShoreGuard repository.

## Backend

```
shoreguard/
├── api/                        # FastAPI application
│   ├── main.py                # App entry point, lifespan, router wiring
│   ├── auth.py                # Authentication, RBAC, session management, user/group CRUD
│   ├── oidc.py                # OpenID Connect client (PKCE, JWKS, state cookies)
│   ├── cli.py                 # Typer CLI commands
│   ├── pages.py               # HTML page routes, auth API endpoints, OIDC endpoints
│   ├── websocket.py           # WebSocket handler for real-time log streaming
│   ├── errors.py              # Exception handlers
│   ├── error_codes.py         # Structured error code definitions
│   ├── deps.py                # FastAPI dependencies (get_client, resolve_gateway)
│   ├── schemas.py             # Pydantic request/response schemas
│   ├── validation.py          # Shared input validation helpers
│   ├── metrics.py             # Prometheus metrics, request-ID tracking, latency middleware
│   ├── lro.py                 # Long-running operation helpers (202 + SSE streaming)
│   ├── ratelimit.py           # Sliding-window login rate limiter
│   ├── password.py            # Password hashing and validation (bcrypt)
│   ├── security_headers.py    # Security headers middleware (CSP, HSTS, X-Frame-Options)
│   ├── logging_config.py      # Structured logging setup
│   └── routes/                # REST API endpoint modules
│       ├── gateway.py         # Gateway registration and management
│       ├── sandboxes.py       # Sandbox CRUD and execution
│       ├── policies.py        # Policy management and presets
│       ├── providers.py       # Provider CRUD
│       ├── approvals.py       # Approval workflow
│       ├── operations.py      # Long-running operation tracking
│       ├── audit.py           # Audit log queries and export
│       ├── webhooks.py        # Webhook CRUD and delivery management
│       └── templates.py       # Sandbox template endpoints
├── services/                  # Business logic layer
│   ├── gateway.py             # GatewayService — connection management
│   ├── sandbox.py             # SandboxService — lifecycle operations
│   ├── policy.py              # PolicyService — policy CRUD
│   ├── providers.py           # ProviderService — provider config
│   ├── approvals.py           # ApprovalService — draft management
│   ├── operations.py          # OperationService — async task tracking (DB-backed)
│   ├── operations_types.py    # Operation type definitions
│   ├── registry.py            # GatewayRegistry — persistent storage
│   ├── local_gateway.py       # LocalGatewayManager — Docker lifecycle
│   ├── audit.py               # AuditService — persistent audit logging
│   ├── webhooks.py            # WebhookService — delivery, retry, formatting
│   ├── formatters.py          # Webhook payload formatters (Slack, Discord, email)
│   ├── sandbox_meta.py        # Sandbox metadata helpers
│   └── _openshell_meta.py     # OpenShell metadata loader (openshell.yaml)
├── client/                    # gRPC client for OpenShell
│   ├── __init__.py            # Client factory and connection management
│   ├── sandboxes.py           # Sandbox gRPC operations
│   ├── policies.py            # Policy gRPC operations
│   ├── approvals.py           # Approval gRPC operations
│   ├── providers.py           # Provider gRPC operations
│   ├── _converters.py         # Protobuf ↔ domain model converters
│   └── _proto/                # Generated protobuf stubs
├── alembic/                   # Database migrations
│   └── versions/              # Migration scripts (001–012)
├── presets/                   # Policy preset YAML files
├── settings.py                # Centralized Pydantic settings (12 sub-models)
├── config.py                  # Configuration and validation
├── db.py                      # SQLAlchemy engine and init
├── models.py                  # ORM models (Gateway, User, Group, ServicePrincipal, AuditEntry, Webhook, ...)
├── exceptions.py              # Custom exception hierarchy
├── presets.py                 # Preset loader
└── sandbox_templates.py       # Sandbox template definitions
```

## Frontend

The frontend lives in `frontend/` and is built with:

- **Vanilla JavaScript** — no framework, small footprint
- **Bootstrap 5** — responsive CSS
- **Alpine.js** — lightweight reactivity for interactive components
- **Jinja2 templates** — server-side rendered HTML pages

During the build step the frontend is bundled into the Python wheel and served
from `shoreguard/_frontend` at runtime.
