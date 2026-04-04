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

## Health probes

These endpoints are **unauthenticated** and designed for container orchestration.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness probe — returns 200 if the process is running |
| `GET` | `/readyz` | Readiness probe — checks database and service initialisation |

## Gateway management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateway/list` | List all registered gateways (supports `?label=key:value` filter) |
| `POST` | `/api/gateway/register` | Register a new gateway (accepts `description`, `labels`) |
| `PATCH` | `/api/gateway/{name}` | Update gateway description and/or labels |
| `DELETE` | `/api/gateway/{name}` | Remove a gateway |
| `GET` | `/api/gateway/{name}/info` | Gateway details (status, endpoint, dates) |
| `GET` | `/api/gateway/{name}/config` | Gateway configuration |
| `POST` | `/api/gateway/{name}/test-connection` | Test connectivity to a gateway |
| `POST` | `/api/gateway/{name}/start` | Start gateway (local mode only) |
| `POST` | `/api/gateway/{name}/stop` | Stop gateway (local mode only) |
| `POST` | `/api/gateway/{name}/restart` | Restart gateway (local mode only) |
| `GET` | `/api/gateways/{gw}/health` | Gateway health status |

### Gateway metadata

Gateways support an optional `description` (free text, max 1 000 chars) and
`labels` (key-value dict, max 20 entries). Labels use Kubernetes-style keys
(`[a-zA-Z0-9][a-zA-Z0-9._-]*`, max 63 chars) and free-text values (max 253
chars).

**Filtering by label:** append `?label=key:value` query parameters to
`GET /api/gateway/list`. Multiple labels are AND-combined:

```
GET /api/gateway/list?label=env:prod&label=team:ml
```

**Updating metadata after registration:**

```http
PATCH /api/gateway/{name}
Content-Type: application/json

{
  "description": "Production EU-West for ML team",
  "labels": {"env": "prod", "team": "ml"}
}
```

Only fields present in the request body are updated — omitted fields remain
unchanged. Set a field to `null` to clear it.

## Sandboxes

All sandbox endpoints are scoped to a gateway via the `{gw}` path parameter.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/sandboxes` | List sandboxes |
| `POST` | `/api/gateways/{gw}/sandboxes` | Create a sandbox (returns 202 + operation ID) |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}` | Get sandbox details |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}` | Delete a sandbox |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/exec` | Execute a command in a sandbox |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/ssh` | Create SSH session |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/ssh` | Revoke SSH session |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/logs` | Get sandbox logs |

## Policies

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy` | Get the active sandbox policy |
| `PUT` | `/api/gateways/{gw}/sandboxes/{name}/policy` | Update the full sandbox policy |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy/revisions` | List policy revisions |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy/diff` | Compare two revisions |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/policy/network-rules` | Add a network rule |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/policy/network-rules/{key}` | Delete a network rule |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/policy/filesystem` | Add a filesystem path |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/policy/filesystem` | Delete a filesystem path |
| `PUT` | `/api/gateways/{gw}/sandboxes/{name}/policy/process` | Update process/Landlock settings |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/policy/presets/{preset}` | Apply a preset |

## Approvals

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/approvals/pending` | List pending approval requests |
| `POST` | `/api/gateways/{gw}/approvals/{chunk_id}/approve` | Approve a pending request |

## Providers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/providers` | List providers |
| `POST` | `/api/gateways/{gw}/providers` | Create a provider |
| `GET` | `/api/gateways/{gw}/providers/{name}` | Get provider details |
| `PUT` | `/api/gateways/{gw}/providers/{name}` | Update a provider |
| `DELETE` | `/api/gateways/{gw}/providers/{name}` | Delete a provider |
| `GET` | `/api/gateways/{gw}/providers/types` | List known provider types |

## Inference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/inference` | Get cluster inference configuration |
| `PUT` | `/api/gateways/{gw}/inference` | Set inference config (provider, model, timeout) |

## Policy presets

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/policies/presets` | List available policy presets |
| `GET` | `/api/policies/presets/{name}` | Get preset details |

## Audit

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/audit` | Query audit log (filter by actor, resource, action) |
| `GET` | `/api/audit/export` | Export audit log as CSV or JSON |

## Webhooks

Admin-only endpoints for managing event subscriptions. Webhooks receive a
signed `POST` request whenever a subscribed event occurs.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/webhooks` | List all webhooks |
| `POST` | `/api/webhooks` | Create a webhook (returns secret) |
| `GET` | `/api/webhooks/{id}` | Get a webhook by ID |
| `PUT` | `/api/webhooks/{id}` | Update a webhook (URL, events, active state, channel) |
| `DELETE` | `/api/webhooks/{id}` | Delete a webhook |
| `POST` | `/api/webhooks/{id}/test` | Send a test event |

### Channel types

Each webhook has a `channel_type` that controls payload formatting and delivery:

| Type | Delivery | Payload format |
|------|----------|---------------|
| `generic` (default) | HTTP POST with HMAC-SHA256 signature | JSON envelope `{event, timestamp, data}` |
| `slack` | HTTP POST to Slack incoming webhook URL | Slack Block Kit with mrkdwn and color coding |
| `discord` | HTTP POST to Discord webhook URL | Discord embed with color-coded fields |
| `email` | SMTP delivery via `extra_config` settings | Plain-text email |

For email channels, `extra_config` must include SMTP settings:

```json
{
  "smtp_host": "smtp.example.com",
  "smtp_port": 587,
  "smtp_user": "user",
  "smtp_pass": "pass",
  "from_addr": "shoreguard@example.com",
  "to_addrs": ["ops@example.com"]
}
```

### Event types

Subscribe to specific events or use `*` for all:

- `sandbox.created`, `sandbox.deleted`
- `approval.pending`, `approval.approved`, `approval.rejected`
- `webhook.test`

### Signature verification

Generic webhooks include an `X-Shoreguard-Signature` header with an
HMAC-SHA256 signature (Slack and Discord channels do not use signing):

```
X-Shoreguard-Signature: sha256=<hex-digest>
```

Verify by computing `HMAC-SHA256(secret, request_body)` and comparing
the hex digest.

## Metrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/metrics` | Prometheus metrics (unauthenticated) |

Exposed metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `shoreguard_info` | Info | Build version |
| `shoreguard_gateways_total` | Gauge | Registered gateways by status |
| `shoreguard_operations_total` | Gauge | Tracked operations by status |
| `shoreguard_webhook_deliveries_total` | Counter | Webhook deliveries by result |
| `shoreguard_http_requests_total` | Counter | HTTP requests by method and status |

Configure Prometheus to scrape `http://<shoreguard-host>:8888/metrics`.

## Operations

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/operations/{id}` | Poll the status of a long-running operation |

## WebSocket

| Protocol | Path | Description |
|----------|------|-------------|
| `WS` | `/ws/{gw}/{name}` | Real-time log stream for a sandbox |
