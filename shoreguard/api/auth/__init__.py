"""User-based authentication with service principals for ShoreGuard.

Two identity types:
- **Users**: email + password → session cookie (Web UI)
- **Service Principals**: API key → Bearer token (Terraform, CI/CD)

Both carry a role: admin, operator, viewer (hierarchical).

Three credential transports:
1. ``Authorization: Bearer <sp-key>`` header — API / Terraform / curl
2. ``sg_session`` cookie (HMAC-signed)      — Web UI after login
3. ``?token=<sp-key>`` query parameter      — WebSocket connections

Submodules: :mod:`~shoreguard.api.auth.core` (passwords, sessions,
lockout, shared state), :mod:`~shoreguard.api.auth.rbac` (FastAPI
dependencies), :mod:`~shoreguard.api.auth.users`,
:mod:`~shoreguard.api.auth.service_principals`,
:mod:`~shoreguard.api.auth.gateway_roles`, and
:mod:`~shoreguard.api.auth.groups`. Everything public is re-exported
here.
"""

from shoreguard.api.auth.core import (
    COOKIE_NAME,
    ROLES,
    SESSION_MAX_AGE,
    authenticate_user,
    check_request_auth,
    clear_lockout,
    create_session_token,
    hash_password,
    init_auth,
    init_auth_for_test,
    is_account_locked,
    is_registration_enabled,
    is_setup_complete,
    record_failed_login,
    reset,
    reset_lockouts,
    set_no_auth,
    state,
    verify_password,
    verify_session_token,
)
from shoreguard.api.auth.gateway_roles import (
    list_gateway_roles_for_sp,
    list_gateway_roles_for_user,
    remove_gateway_role,
    set_gateway_role,
)
from shoreguard.api.auth.groups import (
    add_group_member,
    create_group,
    delete_group,
    get_group,
    list_group_gateway_roles,
    list_group_members,
    list_groups,
    list_user_groups,
    remove_group_gateway_role,
    remove_group_member,
    set_group_gateway_role,
    update_group,
)
from shoreguard.api.auth.rbac import (
    require_auth,
    require_auth_ws,
    require_role,
    require_role_ws,
)
from shoreguard.api.auth.service_principals import (
    create_service_principal,
    delete_service_principal,
    list_service_principals,
    rotate_service_principal,
)
from shoreguard.api.auth.users import (
    INVITE_MAX_AGE,
    accept_invite,
    bootstrap_admin_user,
    create_user,
    delete_user,
    find_or_create_oidc_user,
    list_users,
)

__all__ = (
    "COOKIE_NAME",
    "INVITE_MAX_AGE",
    "ROLES",
    "SESSION_MAX_AGE",
    "accept_invite",
    "add_group_member",
    "authenticate_user",
    "bootstrap_admin_user",
    "check_request_auth",
    "clear_lockout",
    "create_group",
    "create_service_principal",
    "create_session_token",
    "create_user",
    "delete_group",
    "delete_service_principal",
    "delete_user",
    "find_or_create_oidc_user",
    "get_group",
    "hash_password",
    "init_auth",
    "init_auth_for_test",
    "is_account_locked",
    "is_registration_enabled",
    "is_setup_complete",
    "list_gateway_roles_for_sp",
    "list_gateway_roles_for_user",
    "list_group_gateway_roles",
    "list_group_members",
    "list_groups",
    "list_service_principals",
    "list_user_groups",
    "list_users",
    "record_failed_login",
    "remove_gateway_role",
    "remove_group_gateway_role",
    "remove_group_member",
    "require_auth",
    "require_auth_ws",
    "require_role",
    "require_role_ws",
    "reset",
    "reset_lockouts",
    "rotate_service_principal",
    "set_gateway_role",
    "set_group_gateway_role",
    "set_no_auth",
    "state",
    "update_group",
    "verify_password",
    "verify_session_token",
)
