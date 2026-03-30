# Code Structure

This page describes the high-level layout of the ShoreGuard repository.

## Backend

```
shoreguard/
├── api/                    # FastAPI application
│   ├── main.py            # App entry point, lifespan, router wiring
│   ├── auth.py            # Authentication, RBAC, session management
│   ├── cli.py             # Typer CLI commands
│   ├── pages.py           # HTML page routes, auth API endpoints
│   ├── websocket.py       # WebSocket handler
│   ├── errors.py          # Exception handlers
│   ├── deps.py            # FastAPI dependencies (get_client, resolve_gateway)
│   └── routes/            # REST API endpoint modules
│       ├── gateway.py     # Gateway registration and management
│       ├── sandboxes.py   # Sandbox CRUD and execution
│       ├── policies.py    # Policy management and presets
│       ├── providers.py   # Provider CRUD
│       ├── approvals.py   # Approval workflow
│       └── operations.py  # Long-running operation tracking
├── services/              # Business logic layer
│   ├── gateway.py         # GatewayService — connection management
│   ├── sandbox.py         # SandboxService — lifecycle operations
│   ├── policy.py          # PolicyService — policy CRUD
│   ├── providers.py       # ProviderService — provider config
│   ├── approvals.py       # ApprovalService — draft management
│   ├── operations.py      # OperationStore — async task tracking
│   ├── registry.py        # GatewayRegistry — persistent storage
│   ├── local_gateway.py   # LocalGatewayManager — Docker lifecycle
│   └── _openshell_meta.py # OpenShell metadata loader
├── client/                # gRPC client for OpenShell
│   └── _proto/            # Generated protobuf stubs
├── alembic/               # Database migrations
│   └── versions/          # Migration scripts (001-004)
├── presets/               # Policy preset YAML files
├── config.py              # Configuration and validation
├── db.py                  # SQLAlchemy engine and init
├── models.py              # ORM models (Gateway, User, ServicePrincipal)
└── exceptions.py          # Custom exception hierarchy
```

## Frontend

The frontend lives in `frontend/` and is built with:

- **Vanilla JavaScript** -- no framework, small footprint
- **Bootstrap 5** -- responsive CSS
- **Jinja2 templates** -- server-side rendered HTML pages

During the build step the frontend is bundled into the Python wheel and served
from `shoreguard/_frontend` at runtime.
