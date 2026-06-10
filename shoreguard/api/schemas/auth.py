"""Auth, user, service-principal, and group schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# ─── Auth ─────────────────────────────────────────────────────────────────────


class AuthCheckResponse(BaseModel):
    """Authentication status response.

    Attributes:
        authenticated (bool): Whether the caller is authenticated.
        auth_enabled (bool): Whether authentication is enabled on the server.
        role (str | None): Role of the authenticated caller, if any.
        email (str | None): Email of the authenticated caller, if any.
        needs_setup (bool): Whether initial admin setup is still required.
        registration_enabled (bool): Whether self-registration is permitted.
        local_mode (bool | None): Whether the server runs in local (single-user) mode.
        oidc_providers (list[dict[str, str]] | None): Public OIDC providers available for login.
    """

    authenticated: bool
    auth_enabled: bool
    role: str | None = None
    email: str | None = None
    needs_setup: bool
    registration_enabled: bool = False
    local_mode: bool | None = None
    oidc_providers: list[dict[str, str]] | None = None


class OidcProviderInfo(BaseModel):
    """Public OIDC provider info.

    Attributes:
        name (str): Provider identifier used in URLs.
        display_name (str): Human-readable provider name.
    """

    name: str
    display_name: str


class UserResponse(BaseModel):
    """User record (safe fields only).

    Attributes:
        id (int): User ID.
        email (str): User email address.
        role (str): Global role assigned to the user.
        is_active (bool): Whether the account is active.
        pending_invite (bool): Whether the user has a pending invite.
        created_at (str | None): ISO timestamp when the user was created.
        oidc_provider (str | None): Name of the OIDC provider, if federated.
    """

    id: int
    email: str
    role: str
    is_active: bool = True
    pending_invite: bool = False
    created_at: str | None = None
    oidc_provider: str | None = None


class UserCreateResponse(BaseModel):
    """User creation response — includes the invite token.

    Attributes:
        id (int): User ID.
        email (str): User email address.
        role (str): Global role assigned to the user.
        created_at (str | None): ISO timestamp when the user was created.
        invite_token (str | None): One-time invite token for account activation.
    """

    id: int
    email: str
    role: str
    created_at: str | None = None
    invite_token: str | None = None


class GatewayRoleResponse(BaseModel):
    """Per-gateway role override.

    Attributes:
        gateway_name (str): Name of the gateway the override applies to.
        role (str): Overridden role.
        user_id (int | None): User ID the override applies to, if any.
        sp_id (int | None): Service principal ID the override applies to, if any.
        group_id (int | None): Group ID the override applies to, if any.
    """

    gateway_name: str
    role: str
    user_id: int | None = None
    sp_id: int | None = None
    group_id: int | None = None


class ServicePrincipalResponse(BaseModel):
    """Service principal record (without key hash).

    Attributes:
        id (int): Service principal ID.
        name (str): Service principal name.
        role (str): Global role assigned to the service principal.
        key_prefix (str): Short prefix of the API key for identification.
        created_at (str | None): ISO timestamp when the principal was created.
        created_by (int | None): ID of the user who created the principal.
        last_used (str | None): ISO timestamp of the last successful auth.
        expires_at (str | None): ISO timestamp when the key expires, if any.
    """

    id: int
    name: str
    role: str
    key_prefix: str
    created_at: str | None = None
    created_by: int | None = None
    last_used: str | None = None
    expires_at: str | None = None


class ServicePrincipalCreateResponse(ServicePrincipalResponse):
    """Service principal creation/rotation response — includes the plaintext key.

    Attributes:
        key (str): Plaintext API key — returned only at creation/rotation time.
    """

    key: str


class GroupResponse(BaseModel):
    """User group record.

    Attributes:
        id (int): Group ID.
        name (str): Group name.
        role (str): Default role granted to members.
        description (str | None): Human-readable group description.
        created_at (str | None): ISO timestamp when the group was created.
        member_count (int | None): Number of members in the group.
    """

    id: int
    name: str
    role: str
    description: str | None = None
    created_at: str | None = None
    member_count: int | None = None


class GroupDetailResponse(GroupResponse):
    """Group with member list.

    Attributes:
        members (list[dict[str, Any]] | None): List of member records.
    """

    members: list[dict[str, Any]] | None = None


class GroupMemberResponse(BaseModel):
    """Group membership record.

    Attributes:
        group_id (int): ID of the group.
        group_name (str): Name of the group.
        user_id (int): ID of the member user.
        user_email (str): Email of the member user.
    """

    group_id: int
    group_name: str
    user_id: int
    user_email: str
