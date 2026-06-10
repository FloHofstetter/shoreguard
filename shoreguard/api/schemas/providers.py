"""Inference provider and provider-profile schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── Providers ────────────────────────────────────────────────────────────────


class ProviderResponse(BaseModel):
    """Provider record.

    Mirrors the upstream ``openshell.datamodel.v1.Provider`` shape after the
    M37 schema migration: identity moved into ``ObjectMeta`` upstream and is
    flattened back into ``id``/``name``/``created_at_ms``/``labels`` here for
    REST/UI ergonomics.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        id (str | None): Stable gateway-assigned object ID.
        name (str | None): Provider name.
        created_at_ms (int | None): Creation timestamp (ms since epoch).
        labels (dict[str, str] | None): Kubernetes-style labels.
        type (str | None): Provider type identifier.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    id: str | None = None
    name: str | None = None
    created_at_ms: int | None = None
    labels: dict[str, str] | None = None
    type: str | None = None


class ProviderDeleteResponse(BaseModel):
    """Provider deletion confirmation.

    Attributes:
        deleted (bool): Whether the provider was deleted.
    """

    deleted: bool


class ProviderTypeResponse(BaseModel):
    """Provider type metadata.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        type (str | None): Provider type identifier.
        label (str | None): Human-readable label for the provider type.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    type: str | None = None
    label: str | None = None


class ProviderEnvVar(BaseModel):
    """A single environment variable projected into sandboxes by a provider.

    Secret values are never returned — only the key, its source, and a
    redacted placeholder. Use this endpoint to debug agent misconfiguration
    without exposing credentials.

    Attributes:
        key (str): Environment variable name (e.g. ``ANTHROPIC_API_KEY``).
        source (str): Origin of the value: ``credential`` (from
            ``provider.credentials``), ``config`` (from ``provider.config``),
            or ``type_default`` (implied by the provider type's cred_key
            mapping in ``openshell.yaml`` when no matching credential exists).
        redacted_value (str): Constant placeholder (``[REDACTED]``) so callers
            can distinguish "key is set" from "key is absent" without seeing
            the real value.
    """

    key: str
    source: str
    redacted_value: str = "[REDACTED]"


class ProviderProfileCredentialSchema(BaseModel):
    """Credential slot exposed by a provider profile.

    Attributes:
        name (str): Credential slot name.
        description (str): Human-readable description.
        env_vars (list[str]): Environment variable names this slot maps to.
        required (bool): Whether the credential is mandatory.
        auth_style (str): Auth style hint ("bearer", "basic", ...).
        header_name (str): HTTP header name when ``auth_style`` requires it.
        query_param (str): Query parameter name when ``auth_style`` requires it.
    """

    name: str
    description: str = ""
    env_vars: list[str] = Field(default_factory=list)
    required: bool = False
    auth_style: str = ""
    header_name: str = ""
    query_param: str = ""


class ProviderProfileSchema(BaseModel):
    """Reusable provider-type profile from the gateway registry.

    Mirrors upstream ``openshell.gateway.v1.ProviderProfile`` (M37 /
    NVIDIA/OpenShell PR #1170). The ``endpoint_count`` and
    ``binary_count`` fields are convenience aggregates so list views
    don't need the full payload.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        id (str): Stable profile ID (e.g. ``"claude"``).
        display_name (str): Human-readable label.
        description (str): Free-text description.
        category (str): Profile category slug.
        credentials (list[ProviderProfileCredentialSchema]): Required and
            optional credential slots.
        endpoint_count (int): Number of network endpoints declared.
        binary_count (int): Number of declared binaries.
        inference_capable (bool): True when the profile can be used as an
            inference target.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    display_name: str = ""
    description: str = ""
    category: str = "unspecified"
    credentials: list[ProviderProfileCredentialSchema] = Field(default_factory=list)
    endpoint_count: int = 0
    binary_count: int = 0
    inference_capable: bool = False


class ProviderProfileDiagnosticSchema(BaseModel):
    """Lint/import diagnostic from the gateway.

    Attributes:
        source (str): Origin label sent in the request.
        profile_id (str): Profile the diagnostic is about (empty for
            request-level errors).
        field (str): Field path the diagnostic applies to.
        message (str): Human-readable message.
        severity (str): Severity slug (``"error"``, ``"warning"``).
    """

    source: str = ""
    profile_id: str = ""
    field: str = ""
    message: str = ""
    severity: str = ""


class ProviderProfileImportItem(BaseModel):
    """One entry in a lint/import payload.

    Attributes:
        profile (ProviderProfileSchema): Profile to lint or import.
        source (str): Origin label echoed back in diagnostics.
    """

    profile: ProviderProfileSchema
    source: str = ""


class LintProviderProfilesRequest(BaseModel):
    """Body for the lint endpoint.

    Attributes:
        profiles (list[ProviderProfileImportItem]): Profiles to validate.
    """

    profiles: list[ProviderProfileImportItem]


class LintProviderProfilesResponse(BaseModel):
    """Result of a lint pass.

    Attributes:
        valid (bool): True when no error-severity diagnostics were raised.
        diagnostics (list[ProviderProfileDiagnosticSchema]): Per-profile
            diagnostics.
    """

    valid: bool
    diagnostics: list[ProviderProfileDiagnosticSchema] = Field(default_factory=list)


class ImportProviderProfilesRequest(BaseModel):
    """Body for the import endpoint — same shape as lint.

    Attributes:
        profiles (list[ProviderProfileImportItem]): Profiles to import.
    """

    profiles: list[ProviderProfileImportItem]


class ImportProviderProfilesResponse(BaseModel):
    """Result of an import attempt.

    Attributes:
        imported (bool): True when the gateway accepted the batch.
        profiles (list[ProviderProfileSchema]): Profiles after import.
        diagnostics (list[ProviderProfileDiagnosticSchema]): Per-profile
            diagnostics.
    """

    imported: bool
    profiles: list[ProviderProfileSchema] = Field(default_factory=list)
    diagnostics: list[ProviderProfileDiagnosticSchema] = Field(default_factory=list)


class DeleteProviderProfileResponse(BaseModel):
    """Confirmation for a profile delete.

    Attributes:
        deleted (bool): True when the profile was deleted.
    """

    deleted: bool


class ProviderEnvResponse(BaseModel):
    """Environment-variable projection for a provider.

    Attributes:
        provider (str): Provider name.
        type (str | None): Provider type identifier.
        env (list[ProviderEnvVar]): Environment variables the provider
            projects into sandboxes. Values are redacted.
    """

    provider: str
    type: str | None = None
    env: list[ProviderEnvVar]


# ─── Provider credential refresh / rotation ───────────────────────────────────

RefreshStrategy = Literal[
    "static",
    "external",
    "oauth2_refresh_token",
    "oauth2_client_credentials",
    "google_service_account_jwt",
]


class ConfigureProviderRefreshRequest(BaseModel):
    """Body for configuring credential refresh on a provider.

    Attributes:
        credential_key (str): Credential key within the provider to refresh.
        strategy (RefreshStrategy): Refresh strategy to apply.
        material (dict[str, str]): Strategy-specific material (token URLs,
            client ids, etc.). Secret values must be named in
            ``secret_material_keys``.
        secret_material_keys (list[str]): Keys within ``material`` that hold
            secret values; the gateway stores these encrypted.
        expires_at_ms (int | None): Optional absolute expiry of the current
            credential, milliseconds since the epoch.
    """

    credential_key: str = Field(min_length=1, max_length=253)
    strategy: RefreshStrategy
    material: dict[str, str] = Field(default_factory=dict)
    secret_material_keys: list[str] = Field(default_factory=list)
    expires_at_ms: int | None = Field(default=None, ge=0)


class RotateProviderCredentialRequest(BaseModel):
    """Body for rotating a single provider credential.

    Attributes:
        credential_key (str): Credential key to rotate.
    """

    credential_key: str = Field(min_length=1, max_length=253)


class ProviderRefreshStatusResponse(BaseModel):
    """Refresh status for one provider credential.

    Attributes:
        provider_name (str): Provider name.
        provider_id (str): Provider object id.
        credential_key (str): Credential key this status describes.
        strategy (str): Refresh strategy in effect.
        status (str): Gateway-reported status string.
        expires_at_ms (int): Credential expiry (0 = non-expiring/unset).
        next_refresh_at_ms (int): Next scheduled refresh (0 = none).
        last_refresh_at_ms (int): Last successful refresh (0 = never).
        last_error (str): Last refresh error message, empty when healthy.
    """

    provider_name: str
    provider_id: str
    credential_key: str
    strategy: str
    status: str
    expires_at_ms: int
    next_refresh_at_ms: int
    last_refresh_at_ms: int
    last_error: str


class ProviderRefreshStatusListResponse(BaseModel):
    """List of credential-refresh status entries for a provider.

    Attributes:
        credentials (list[ProviderRefreshStatusResponse]): One entry per
            configured credential.
    """

    credentials: list[ProviderRefreshStatusResponse]


class ProviderRefreshDeleteResponse(BaseModel):
    """Confirmation for deleting a refresh configuration.

    Attributes:
        deleted (bool): Whether a configuration existed and was deleted.
    """

    deleted: bool
