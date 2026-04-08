# Security Model

ShoreGuard is a security product — it manages access control for AI agent
sandboxes. This page describes the security mechanisms built into ShoreGuard
itself.

---

## Authentication

ShoreGuard supports three authentication methods. All resolve to the same
role-based permission model.

### Session cookies

Browser users authenticate via email + password (or [OIDC / SSO](../admin/oidc.md)).
On success, ShoreGuard issues an **HMAC-signed cookie** (`sg_session`). The
server is stateless — the cookie contains the user identity and a signature
verified on every request. Cookies expire after 7 days by default
(`SHOREGUARD_SESSION_MAX_AGE`).

When ShoreGuard detects the request arrived via HTTPS (from the
`X-Forwarded-Proto` header), it sets the `Secure` flag on cookies
automatically.

### API keys (service principals)

Service principals use bearer tokens passed in the `Authorization` header.
Keys are **SHA-256 hashed** before storage — the plaintext is shown once at
creation and never stored. See [Service Principals](../admin/service-principals.md).

### OIDC / SSO

OpenID Connect authentication uses the **Authorization Code Flow with PKCE
(S256)**. Security measures:

- **PKCE** prevents authorization code interception
- **HMAC-signed state cookie** provides CSRF protection (stateless, no cleanup)
- **Nonce validation** prevents replay attacks
- **JWT signature verification** via the provider's JWKS endpoint
- **Issuer and audience checks** on every ID token
- **Clock skew leeway** of 30 seconds for token expiry

See the [OIDC / SSO guide](../admin/oidc.md) for configuration.

---

## Password security

- Passwords are hashed with **bcrypt** (via passlib) before storage
- Minimum length: 8 characters (configurable via `SHOREGUARD_PASSWORD_MIN_LENGTH`)
- Optional complexity requirements: mixed case, digits, special characters
  (`SHOREGUARD_PASSWORD_REQUIRE_COMPLEXITY`)

---

## Rate limiting & account lockout

ShoreGuard protects the login endpoint with two layers:

### IP-based rate limiting

A sliding-window rate limiter tracks login attempts per client IP. After
exceeding the threshold (default: 10 attempts in 5 minutes), the IP is
blocked for 15 minutes. All values are configurable — see
[Configuration](../reference/configuration.md#auth).

### Account lockout

Independent of IP limiting, individual accounts are locked after repeated
failed attempts (default: 5 failures → 15 minutes lockout). This prevents
credential stuffing even when attacks come from rotating IPs.

---

## Authorization (RBAC)

Every user and service principal has exactly one global role:

| Permission | Admin | Operator | Viewer |
|------------|:-----:|:--------:|:------:|
| View dashboard, sandboxes, logs | yes | yes | yes |
| Create/delete sandboxes, edit policies | yes | yes | no |
| Approve/reject access requests | yes | yes | no |
| Register gateways, manage users/keys | yes | no | no |

Roles can be overridden per gateway — see
[Gateway-Scoped Roles](../admin/gateway-roles.md).

The user's role is verified from the database on **every request**, so role
changes take effect immediately without requiring re-login.

---

## Security headers

ShoreGuard injects the following headers on every response:

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |
| `Content-Security-Policy` | Configurable (see [Configuration](../reference/configuration.md#auth)) |
| `Strict-Transport-Security` | Opt-in via `SHOREGUARD_HSTS_ENABLED` |

---

## Gateway communication

ShoreGuard communicates with OpenShell gateways over **gRPC**. Three
authentication modes are supported:

| Mode | Security level |
|------|---------------|
| **mTLS** | Mutual TLS with CA, client certificate, and client key — recommended for production |
| **API key** | Key passed in gRPC metadata |
| **None** | No authentication — development only |

Certificate and key material is validated on upload (size limits, format
checks) and stored in the database.

---

## SSRF protection

When registering gateways, ShoreGuard validates that the endpoint does not
point to private IP ranges (`10.x`, `172.16-31.x`, `192.168.x`, `127.x`).
This prevents server-side request forgery attacks. The check is relaxed in
[local mode](../admin/local-mode.md) where `127.0.0.1` is expected.

---

## Webhook signature verification

Generic webhooks include an `X-Shoreguard-Signature` header containing an
HMAC-SHA256 signature of the request body. Recipients should verify this
signature to confirm the payload was sent by ShoreGuard and was not tampered
with. See [Webhooks](../guides/webhooks.md) for details.

---

## Audit trail

Every state-changing operation is recorded in the audit log with actor, role,
action, resource, and client IP. The audit log supports filtering and export
(CSV/JSON). See [Audit Log](../guides/audit.md).

---

## Development-only features

!!! danger "Never use in production"

    Setting `SHOREGUARD_NO_AUTH=1` disables **all** authentication and
    authorization. Every request is treated as an admin. This is intended for
    local development only.
