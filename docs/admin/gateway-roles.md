# Gateway-Scoped Roles

By default, a user's global role applies to all gateways. Gateway-scoped roles
let you **override** permissions on a per-gateway basis — giving a user (or
service principal) a different role on specific gateways.

---

## When to use

- **Multi-tenant setups** — give team A admin access to their gateways but
  only viewer access to team B's gateways
- **Least privilege** — a CI service principal needs operator access to the
  staging gateway but should be locked out of production
- **Temporary access** — grant an operator admin access to a single gateway
  for debugging

---

## How role resolution works

When ShoreGuard checks permissions for a request targeting a specific gateway,
it resolves the effective role in this order:

1. **Gateway-scoped role for the user** — if set, this wins
2. **Global role** — the user's default role applies

The effective role is the **gateway-scoped role** if one exists, otherwise the
**global role**. Gateway roles can be higher or lower than the global role.

!!! example

    A user with global role **viewer** and a gateway-scoped role of
    **operator** on `prod-gw-01` can create sandboxes on that gateway but
    is read-only on all others.

---

## Managing via the Web UI

1. Open the **Users** page
2. Click the **shield icon** next to a user or service principal
3. In the modal, add or remove gateway-role assignments
4. Each entry specifies a gateway and a role (`admin`, `operator`, `viewer`)

---

## Managing via the API

### User gateway roles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/auth/users/{id}/gateway-roles` | List gateway roles for a user |
| `PUT` | `/api/auth/users/{id}/gateway-roles` | Set gateway roles for a user |

### Service principal gateway roles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/auth/service-principals/{id}/gateway-roles` | List gateway roles |
| `PUT` | `/api/auth/service-principals/{id}/gateway-roles` | Set gateway roles |

### Request body format

```json
[
  {"gateway_name": "prod-gw-01", "role": "operator"},
  {"gateway_name": "staging-gw", "role": "admin"}
]
```

The `PUT` endpoint replaces all gateway roles for the user/SP. Send an empty
array to remove all overrides.

---

## Roles

The same three roles apply at the gateway level:

| Role | Permissions on this gateway |
|------|----------------------------|
| `admin` | Full access including gateway management |
| `operator` | Create sandboxes, edit policies, approve requests |
| `viewer` | Read-only access |

See [Users & RBAC](rbac.md) for the complete permission matrix.
