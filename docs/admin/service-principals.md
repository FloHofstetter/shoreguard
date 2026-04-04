# Service Principals

## What are service principals?

A **service principal** is a named API key with an assigned role, designed for
programmatic access. Use service principals for CI/CD pipelines, Terraform
runs, monitoring scripts, or any automation that needs to talk to the
ShoreGuard API without a human login.

## Creating a service principal

### Via the Web UI

Admins can create service principals at `/users/new-service-principal`. Choose
a descriptive name and assign a role (the same Admin / Operator / Viewer roles
used for human accounts).

### Via the CLI

```bash
shoreguard create-service-principal my-ci-bot --role viewer
```

The API key is displayed **once** at creation time. Copy it immediately and
store it in a secrets manager — ShoreGuard cannot show it again.

## Key format and prefix

New API keys use the format `sg_<random>` (e.g. `sg_dGhpcyBpcyBh...`). The
first 12 characters are stored as `key_prefix` for identification in the UI
and API responses — you can tell which key is which without exposing the full
secret. Legacy keys created before v0.16.0 continue to work.

## How keys are stored

API keys are **SHA-256 hashed** before being written to the database. The
plaintext key is never stored. On each request, ShoreGuard hashes the
presented key and compares it to the stored hash.

A `last_used` timestamp is updated on every successful authentication so you
can audit which keys are active.

## Using a service principal

### REST API

Pass the key in the `Authorization` header:

```http
GET /api/gateways
Authorization: Bearer sg_live_abc123...
```

### Terraform provider

Set the key as an environment variable:

```bash
export SHOREGUARD_API_KEY="sg_live_abc123..."
terraform plan
```

Or configure it directly in the provider block:

```hcl
provider "shoreguard" {
  api_key = var.shoreguard_api_key
}
```

### WebSocket

For WebSocket connections, pass the key as a query parameter:

```
ws://localhost:8888/ws/approvals?token=sg_live_abc123...
```

## Key rotation

Rotate a key without deleting and recreating the service principal. The old
key is invalidated immediately, and a new key is returned once.

```http
POST /api/auth/service-principals/{id}/rotate
Authorization: Bearer <admin-token>
```

Response:

```json
{
  "key": "sg_new_key_here...",
  "id": 3,
  "name": "my-ci-bot",
  "role": "viewer",
  "key_prefix": "sg_new_key_he"
}
```

In the UI, click the rotate button (↻) next to the service principal. A
confirmation dialog appears, then the new key is shown in a copy-once modal.

## Key expiry

Service principals can optionally expire. Set `expires_at` during creation
(ISO-8601 timestamp) or leave it empty for a non-expiring key.

```http
POST /api/auth/service-principals
Content-Type: application/json

{
  "name": "temp-ci-key",
  "role": "operator",
  "expires_at": "2026-06-30T23:59:59Z"
}
```

Expired keys are rejected at authentication time — the request receives a
`401 Unauthorized` response. The Users page shows expiry badges: green
(>30 days), yellow (<30 days), and red (expired).

## Deleting a service principal

Remove a service principal from the user management UI or via the API:

```http
DELETE /api/auth/service-principals/{id}
```

The key is invalidated immediately. Any in-flight requests using it will fail
on the next authentication check.
