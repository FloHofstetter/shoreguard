# Security Model

ShoreGuard is a security product — it manages access control for AI agent
sandboxes. This page describes the security mechanisms built into ShoreGuard
itself.

---

## Security Check (posture self-audit)

**Admin → Security Check** (`GET /api/security/posture`) audits the running
deployment and answers "am I exposed?": auth mode vs. bind address,
`--unsafe-lan` overrides, secret-key hygiene, open self-registration,
HSTS/CSP, per-gateway transport (mTLS vs. the local-mode plaintext
exemption), and whether Tailscale is available as the recommended
remote-access path. Every finding carries a severity (`ok` / `info` /
`warn` / `error`) and an actionable fix hint. The same conditions that
abort startup via `enforce_production_safety()` show up here red — the
page exists so a homelab operator sees them *before* moving the box onto
a network.

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

ShoreGuard validates every outbound target an operator can configure —
gateway endpoints, webhook URLs, SMTP hosts, and OIDC provider URLs
(issuer, JWKS, token endpoint) — and rejects private IP ranges (`10.x`,
`172.16-31.x`, `192.168.x`, `127.x`, link-local, reserved). This prevents
server-side request forgery attacks. Hostnames are resolved first, so a DNS
name pointing into a private range is rejected too (only the first DNS
result is checked). Webhook and SMTP targets are re-checked at delivery
time as DNS-rebinding protection. Kubernetes Service DNS
(`*.svc.cluster.local`) bypasses the gateway-endpoint check, since only
kube-dns/CoreDNS can own that suffix.

Two settings tune the check:

- **`SHOREGUARD_SSRF_ALLOWED_IPS`** — comma-separated IPs/CIDR ranges that
  are *exempted* from the private/loopback rejection. Use this when a
  legitimate dependency lives on a private address, e.g. a homelab OIDC
  provider (`SHOREGUARD_SSRF_ALLOWED_IPS=192.168.1.10/32`) or an internal
  webhook receiver. Matching happens against the **resolved** address, so a
  hostname is exempt only if it resolves into an allowlisted range — DNS
  cannot widen the exemption. The literal hostname `localhost` is always
  treated as private; to exempt loopback, use an IP literal and allowlist
  `127.0.0.1`/`::1`. IPv4-mapped IPv6 forms (`::ffff:192.168.1.10`) are not
  normalised — allowlist the form you actually connect to.
- **`SHOREGUARD_ALWAYS_BLOCKED_IPS`** — ranges that are **always** blocked
  (cloud metadata VIPs, management subnets). Takes precedence over the
  allowlist and over local mode.

The check is also relaxed globally in [local mode](../admin/local-mode.md)
where `127.0.0.1` is expected — but for a real deployment that merely needs
one private dependency reachable, prefer the allowlist over `--local`.
Note: webhook/SMTP *delivery-time* re-checks are not relaxed by local mode;
the allowlist is the supported way to deliver to private addresses.

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
