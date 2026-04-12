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
| `POST` | `/api/auth/service-principals` | Create a service principal (optional `expires_at`) |
| `DELETE` | `/api/auth/service-principals/{id}` | Delete a service principal |
| `POST` | `/api/auth/service-principals/{id}/rotate` | Rotate API key (generates new, invalidates old) |

## OIDC endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/auth/oidc/providers` | List configured OIDC providers (public info only) |
| `GET` | `/api/auth/oidc/login/{provider}` | Initiate OIDC login flow (redirects to provider) |
| `GET` | `/api/auth/oidc/callback` | Handle provider callback (internal, not called directly) |

See the [OIDC / SSO guide](../admin/oidc.md) for configuration and usage.

## Health probes

These endpoints are **unauthenticated** and designed for container orchestration.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness probe — returns 200 if the process is running |
| `GET` | `/readyz` | Readiness probe — checks database and service initialisation |
| `GET` | `/version` | Build identity — returns `{version, git_sha, build_time}` for the running image |

`/version` is the fastest way to verify which artifact a deploy actually landed — see the
[Rollback Runbook](../admin/rollback.md) for how it fits into an incident response.

## Gateway management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateway/list` | List all registered gateways (supports `?label=key:value` filter) |
| `POST` | `/api/gateway/register` | Register a new gateway (accepts `description`, `labels`) |
| `PATCH` | `/api/gateway/{name}` | Update gateway description and/or labels |
| `DELETE` | `/api/gateway/{name}` | Remove a gateway |
| `GET` | `/api/gateway/{name}/info` | Gateway details (status, endpoint, dates) |
| `GET` | `/api/gateway/{name}/config` | Gateway configuration |
| `GET` | `/api/gateway/{name}/settings` | Get gateway settings (admin, v0.29.0+) |
| `PUT` | `/api/gateway/{name}/settings/{key}` | Update a single setting (admin, v0.29.0+) |
| `DELETE` | `/api/gateway/{name}/settings/{key}` | Delete a single setting (admin, v0.29.0+) |
| `POST` | `/api/gateway/{name}/test-connection` | Test connectivity to a gateway |
| `POST` | `/api/gateway/{name}/start` | Start gateway (local mode only) |
| `POST` | `/api/gateway/{name}/stop` | Stop gateway (local mode only) |
| `POST` | `/api/gateway/{name}/restart` | Restart gateway (local mode only) |
| `GET` | `/api/gateways/{gw}/health` | Gateway health status |
| `POST` | `/api/gateway/discover` | Discover gateways via DNS SRV records (operator+, M22) |
| `GET` | `/api/gateway/discovery/status` | Discovery loop status + last scan results (viewer, M22) |

### Discovery (M22, v0.30.2+)

`POST /api/gateway/discover` resolves `_openshell._tcp.<domain>` SRV
records for every configured discovery domain and, when
`auto_register=true`, registers any new endpoints that pass the
existing `_validate_endpoint_format` guard (same `*.svc.cluster.local`
whitelist as manual registration). Pass an optional
`{"domains": ["cluster.local"]}` body to override the configured list
for this scan only. Audit-logged as `gateway.discovered`.

```http
POST /api/gateway/discover
Content-Type: application/json

{"domains": ["svc.cluster.local"]}
```

Configure the background loop via `SHOREGUARD_DISCOVERY_*` environment
variables — see [Settings Reference](settings.md).

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

## Sandbox templates

Pre-configured sandbox bundles (image, GPU, providers, presets, environment).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sandbox-templates` | List available templates |
| `GET` | `/api/sandbox-templates/{name}` | Get full template configuration |

Built-in templates: `data-science`, `web-dev`, `secure-coding`. Templates are
YAML files shipped with ShoreGuard — see the
[sandbox guide](../guides/sandboxes.md#sandbox-templates) for details.

## Sandboxes

All sandbox endpoints are scoped to a gateway via the `{gw}` path parameter.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/sandboxes` | List sandboxes |
| `POST` | `/api/gateways/{gw}/sandboxes` | Create a sandbox (returns 202 + operation ID) |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}` | Get sandbox details |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}` | Delete a sandbox |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/exec` | Execute a command in a sandbox (supports `tty: true` for interactive programs, v0.29.0+) |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/ssh` | Create SSH session |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/ssh` | Revoke SSH session |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/logs` | Get sandbox logs |

## Boot Hooks (M22, v0.30.2+)

Pre- and post-create hooks that run as part of sandbox creation.
Pre-create hooks execute in the ShoreGuard process via `subprocess.run`
with a whitelisted env (`SG_SANDBOX_NAME`, `SG_SANDBOX_IMAGE`,
`SG_SANDBOX_POLICY_ID`, plus user-defined env). A failing pre-create
hook aborts `CreateSandbox` with `BootHookError`. Post-create hooks
run inside the sandbox via the existing `ExecSandbox` RPC — intended
for warm-up tasks like `apt update`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/hooks` | List hooks (viewer) |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/hooks/{id}` | Get a hook |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/hooks` | Create a hook (admin) |
| `PUT` | `/api/gateways/{gw}/sandboxes/{name}/hooks/{id}` | Update a hook (admin) |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/hooks/{id}` | Delete a hook (admin) |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/hooks/reorder` | Reorder hooks (admin) |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/hooks/{id}/run` | Manually trigger a hook (operator+) |

Admin-only `skip_hooks: true` on `POST .../sandboxes` bypasses both
phases for recovery scenarios. Audit events:
`boot_hook.created|updated|deleted|reordered|manual_run`.

## Policies

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy` | Get the active sandbox policy |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy/effective` | Get the effective policy — what the gateway enforces (v0.29.0+) |
| `PUT` | `/api/gateways/{gw}/sandboxes/{name}/policy` | Update the full sandbox policy |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy/revisions` | List policy revisions |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy/diff` | Compare two revisions |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/policy/network-rules` | Add a network rule |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/policy/network-rules/{key}` | Delete a network rule |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/policy/filesystem` | Add a filesystem path |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/policy/filesystem` | Delete a filesystem path |
| `PUT` | `/api/gateways/{gw}/sandboxes/{name}/policy/process` | Update process/Landlock settings |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/policy/presets/{preset}` | Apply a preset |

All policy *write* endpoints — plus `POST .../approve` and
`.../approve-all` — return **HTTP 423** when a policy pin (M18) is
active on the sandbox.

### Policy Pinning (M18, v0.30.2+)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy/pin` | Get active pin, if any |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/policy/pin` | Pin active version (operator+) |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/policy/pin` | Remove pin (operator+) |

```http
POST /api/gateways/dev/sandboxes/agent-a/policy/pin
Content-Type: application/json

{
  "reason": "Change freeze for release-2026-04",
  "expires_at": "2026-04-20T00:00:00Z"
}
```

Audit-logged as `policy_pin.created` / `policy_pin.deleted`. Pins
auto-expire server-side. Read endpoints (`GET /policy`, `/export`)
remain allowed.

### Policy Prover (M17, v0.30.2+)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/policy/verify` | Run Z3 formal verification on the active policy (operator+) |
| `GET` | `/api/gateways/{gw}/policies/presets/verify` | List available verification templates |

Four built-in query templates: `can_exfiltrate`,
`unrestricted_egress`, `binary_bypass`, `write_despite_readonly`. Each
returns SAT (with a witness model) or UNSAT (property holds).

```http
POST /api/gateways/dev/sandboxes/agent-a/policy/verify
Content-Type: application/json

{"template": "can_exfiltrate", "params": {}}
```

See the [Policy Prover guide](../guides/policy-prover.md) for
template semantics and example witness models.

### GitOps (M23, v0.30.2+)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/policy/export` | Export policy as deterministic YAML |
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/policy/apply` | Apply a YAML policy (dry-run or write) |

`POST /apply` accepts `{yaml, dry_run, expected_version}`. Response
status codes: `200 up_to_date` / `200 dry_run` / `200 applied` /
`202 vote_recorded` (M19 workflow active) / `409` version mismatch /
`423` pinned / `400` malformed YAML. `expected_version` falls back to
`metadata.policy_hash` in the YAML body. Under an active M19 workflow
the first apply records one approve-vote on a synthetic chunk id
`policy.apply:<sha16>` and returns 202; subsequent apply calls with
the same YAML body accumulate votes until quorum, at which point
`UpdateConfig` fires upstream exactly once.

Paired with the `shoreguard policy export|diff|apply` CLI —
see the [GitOps guide](../guides/gitops.md).

## Approvals

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/approvals/pending` | List pending approval requests |
| `POST` | `/api/gateways/{gw}/approvals/{chunk_id}/approve` | Approve a pending request |
| `GET` | `/api/gateways/{gw}/approvals/{chunk_id}/decisions` | Running tally + voter list under an active workflow (M19) |

Both `POST /approve` and `POST /approve-all` accept
`?wait_loaded=true` (M14): the server polls the gateway's policy
status internally (up to 30 s) and only returns once the new policy
version is reported as `loaded`, eliminating the client-side polling
loop. Returns 504 on timeout.

### Approval Workflows (M19, v0.30.2+)

Per-sandbox multi-stage approvals. Configure a required voter count
(quorum) + voter set + optional escalation deadline; `POST .../approve`
under an active workflow returns **HTTP 202 `vote_recorded`** until
quorum is reached, at which point the upstream `ApproveChunk` fires
exactly once. A single reject is unanimous and kills the proposal.
`POST .../approve-all` is admin-only when a workflow is active
(returns HTTP 409 to non-admins).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/approval-workflow` | Get workflow config (viewer) |
| `PUT` | `/api/gateways/{gw}/sandboxes/{name}/approval-workflow` | Upsert workflow (admin) |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/approval-workflow` | Remove workflow (admin) |

Webhook events: `approval.vote_cast`, `approval.quorum_met`,
`approval.escalated` (fires reactively on the next vote after the
escalation deadline — no background scheduler).

## Bypass Detection (M15, v0.30.2+)

OCSF events classified as potential policy bypasses (denial followed
by success, egress via unusual ports, DNS exfiltration signatures)
are streamed into an in-memory ring buffer (last 1 000 events per
gateway).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/bypass` | Paginated event list with `?severity=` filter |
| `GET` | `/api/gateways/{gw}/bypass/summary` | Per-severity counts + top offending sandboxes |

Each event carries a MITRE ATT&CK technique mapping for downstream
SIEM correlation. See the [Bypass Detection guide](../guides/bypass-detection.md).

## Providers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/providers` | List providers |
| `POST` | `/api/gateways/{gw}/providers` | Create a provider |
| `GET` | `/api/gateways/{gw}/providers/{name}` | Get provider details |
| `GET` | `/api/gateways/{gw}/providers/{name}/env` | Redacted env-var projection for a provider (v0.29.0+) |
| `PUT` | `/api/gateways/{gw}/providers/{name}` | Update a provider |
| `DELETE` | `/api/gateways/{gw}/providers/{name}` | Delete a provider |
| `GET` | `/api/gateways/{gw}/providers/types` | List known provider types |

`GET /providers/{name}/env` returns the environment variables the provider
projects into sandboxes — keys only, values redacted as `[REDACTED]`. Each
entry carries a `source` of `credential`, `config`, or `type_default`
(implied by the provider type's `cred_key` in `openshell.yaml`). Useful for
debugging agent misconfiguration without exposing secrets.

## Inference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/gateways/{gw}/inference` | Get cluster inference configuration (accepts `?route_name=` since v0.29.0) |
| `PUT` | `/api/gateways/{gw}/inference` | Set inference config (provider, model, timeout, optional `route_name`) |
| `GET` | `/api/gateways/{gw}/inference/bundle` | Resolved inference bundle: cluster default + route list + per-route credential state (M20) |

`GET /inference/bundle` (M20) returns the fully resolved inference
configuration as a single payload, with API keys redacted to
`has_api_key: bool` at the wrapper boundary. The gateway detail page
renders this as a route table with a shield badge per route that
carries credentials. Audit-logged as `inference.bundle.read`.

Since v0.29.0, `GET /inference` accepts an optional `?route_name=` query
parameter. An empty value (the default) returns the cluster's default
inference route; passing a name like `sandbox-system` returns the route
that OpenShell v0.0.25+ uses for sandbox system-level model calls.

## SBOM (M21, v0.30.2+)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/gateways/{gw}/sandboxes/{name}/sbom` | Upload a CycloneDX JSON SBOM (admin, replaces prior snapshot, max 10 MiB) |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/sbom` | Get snapshot metadata |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/sbom/components` | Paginated component list with `?search=`, `?severity=` (CRITICAL/HIGH/MEDIUM/LOW/INFO/UNKNOWN/CLEAN), `?offset=`, `?limit=` (max 500) |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/sbom/vulnerabilities` | Structured vulnerability list, sorted highest-severity first |
| `GET` | `/api/gateways/{gw}/sandboxes/{name}/sbom/raw` | Original CycloneDX payload as `application/vnd.cyclonedx+json` |
| `DELETE` | `/api/gateways/{gw}/sandboxes/{name}/sbom` | Delete the snapshot (admin) |

Vulnerabilities are read offline from the CycloneDX `vulnerabilities` array
(no online NVD/OSV lookup). One snapshot per `(gateway, sandbox)` — uploads
replace the prior snapshot. Both write paths are audit-logged as
`sbom.uploaded` / `sbom.deleted`.

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
| `GET` | `/api/webhooks/{id}/deliveries` | List delivery attempts (newest first, `?limit=50`) |

For channel types, event types, signature verification, and delivery retry
logic, see the [Webhooks guide](../guides/webhooks.md).

## Metrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/metrics` | Prometheus metrics (unauthenticated) |

See the [Prometheus integration guide](../integrations/prometheus.md) for the
full metric list and scrape configuration.

## Operations

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/operations/{id}` | Poll the status of a long-running operation |

## WebSocket

| Protocol | Path | Description |
|----------|------|-------------|
| `WS` | `/ws/{gw}/{name}` | Real-time log stream for a sandbox |

## Error responses

Since v0.29.0, error responses follow
[RFC 9457 Problem Details](https://datatracker.ietf.org/doc/html/rfc9457).
Bodies are served with `Content-Type: application/problem+json` and carry
the standard fields plus the ShoreGuard `code`:

| Field | Description |
|-------|-------------|
| `type` | URI reference identifying the problem type |
| `title` | Short, human-readable summary (matches the HTTP status phrase) |
| `status` | HTTP status code as an integer |
| `detail` | Human-readable explanation specific to this occurrence |
| `code` | Machine-readable ShoreGuard error code (e.g. `not_found`, `forbidden`) |

Endpoints may include additional extension members such as `request_id`
(for correlation with server logs) and `errors` (field-level validation
details on 422 responses). The `detail` field is preserved from
pre-v0.29.0 responses, so clients that only read `body.detail` continue
to work unchanged.
