# REST API Reference

!!! note "Interactive docs"

    When OpenAPI is enabled, an interactive Swagger UI is available at `/docs`.
    OpenAPI docs are hidden when authentication is enabled to avoid leaking
    endpoint details to unauthenticated users.

## Authentication

ShoreGuard supports two authentication methods:

- **Session cookies** — used by the browser UI after login.
- **Bearer tokens** — used by API clients, Terraform, and CI pipelines. Pass
  the token in the `Authorization: Bearer <key>` header.

---

## Auth endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/login` | Log in with email + password |
| `POST` | `/api/auth/logout` | Destroy session |
| `GET` | `/api/auth/check` | Check current session / token validity |
| `POST` | `/api/auth/setup` | Create the initial admin account (first run only) |
| `POST` | `/api/auth/register` | Self-register (when registration is enabled) |
| `POST` | `/api/auth/accept-invite` | Accept an invite and set a password |
| `GET` | `/api/auth/users` | List users |
| `POST` | `/api/auth/users` | Create or invite a user |
| `DELETE` | `/api/auth/users` | Delete a user |
| `GET` | `/api/auth/service-principals` | List service principals |
| `POST` | `/api/auth/service-principals` | Create a service principal |
| `DELETE` | `/api/auth/service-principals` | Delete a service principal |

## Gateway management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateway/list` | List all registered gateways |
| `POST` | `/api/gateway/register` | Register a new gateway |
| `DELETE` | `/api/gateway/{name}` | Remove a gateway |
| `POST` | `/api/gateway/{name}/select` | Set the active gateway for the session |
| `POST` | `/api/gateway/{name}/test-connection` | Test connectivity to a gateway |

## Sandboxes and policies

All sandbox endpoints are scoped to a gateway.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/sandboxes` | List sandboxes |
| `POST` | `/api/gateways/{gw}/sandboxes` | Create a sandbox |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}` | Delete a sandbox |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/exec` | Execute a command in a sandbox |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy` | Get the sandbox policy |
| `PUT` | `/api/gateways/{gw}/sandboxes/{name}/policy` | Update the sandbox policy |

## Approvals

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/approvals/pending` | List pending approval requests |
| `POST` | `/api/gateways/{gw}/approvals/{chunk_id}/approve` | Approve a pending request |

## Policy presets

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/policies/presets` | List available policy presets |

## Operations

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/operations/{id}` | Poll the status of a long-running operation |

## WebSocket

| Protocol | Path | Description |
|----------|------|-------------|
| `WS` | `/ws/{gw}/{name}` | Real-time log stream for a sandbox |
