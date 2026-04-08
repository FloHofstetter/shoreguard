# RBAC & User Management

ShoreGuard uses role-based access control to determine what each user can do.
Every user account is assigned exactly one role.

## Roles

| Permission | Admin | Operator | Viewer |
|------------|:-----:|:--------:|:------:|
| View dashboard and sandboxes | yes | yes | yes |
| View logs and events | yes | yes | yes |
| Create and delete sandboxes | yes | yes | no |
| Edit security policies | yes | yes | no |
| Approve or reject access requests | yes | yes | no |
| Register and remove gateways | yes | no | no |
| Manage users and invites | yes | no | no |
| Create and delete API keys | yes | no | no |

## Setup wizard

On the very first visit ShoreGuard presents a setup wizard that creates the
**initial admin account**. No other users exist until the admin invites them.

## Inviting users

1. An admin opens **Users** and clicks **Invite**.
2. A one-time invite token is generated.
3. The invited person opens the token link and sets their password at `/invite`.
4. The new account is created with the role chosen by the admin.

## Self-registration

By default, self-registration is disabled. To allow it:

```bash
export SHOREGUARD_ALLOW_REGISTRATION=1
shoreguard
```

Self-registered accounts are created with the **Viewer** role. An admin can
promote them later.

## User management UI

Admins can manage all accounts at `/users`. From there you can change roles,
revoke access, or delete accounts.

## CLI commands

```bash
shoreguard create-user admin@example.com --role admin
shoreguard delete-user admin@example.com
shoreguard list-users
```

## OIDC / SSO

ShoreGuard supports OpenID Connect for single sign-on with Google, Entra ID,
Okta, and other providers. See the [OIDC / SSO guide](oidc.md).

## Gateway-scoped roles

Roles can be overridden per gateway to support multi-tenant setups and
least-privilege access. See [Gateway-Scoped Roles](gateway-roles.md).

## Session security

For details on session cookies, password hashing, rate limiting, and other
security mechanisms, see the [Security Model](../concepts/security.md).
