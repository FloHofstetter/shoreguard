"""Centralized application settings for ShoreGuard.

All tuneable configuration lives here as Pydantic Settings models.
Values are read from environment variables; each sub-model uses its own
``env_prefix`` so that, for example, ``SHOREGUARD_GATEWAY_BACKOFF_MIN=10``
overrides ``GatewaySettings.backoff_min``.

Usage::

    from shoreguard.settings import get_settings

    settings = get_settings()
    print(settings.server.port)        # 8888
    print(settings.gateway.backoff_min) # 5.0
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ─── Sub-models ───────────────────────────────────────────────────────────────


class ServerSettings(BaseSettings):
    """Server bind address, logging, and runtime flags."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_")

    host: str = "0.0.0.0"
    port: int = 8888
    log_level: str = "info"
    log_format: str = "text"
    reload: bool = True
    database_url: str | None = None
    local_mode: bool = False
    graceful_shutdown_timeout: int = 5
    gzip_minimum_size: int = 1000


class AuthSettings(BaseSettings):
    """Authentication, sessions, and registration."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_")

    no_auth: bool = False
    secret_key: str | None = None
    allow_registration: bool = False
    admin_password: str | None = None
    cookie_name: str = "sg_session"
    session_max_age: int = 86400 * 7  # 7 days
    invite_max_age: int = 86400 * 7  # 7 days
    password_min_length: int = 8
    password_require_complexity: bool = False
    login_rate_limit_attempts: int = 10
    login_rate_limit_window: int = 300  # 5 minutes
    login_rate_limit_lockout: int = 900  # 15 minutes
    account_lockout_attempts: int = 5
    account_lockout_duration: int = 900  # 15 minutes
    metrics_public: bool = False
    hsts_enabled: bool = False
    hsts_max_age: int = 63072000  # 2 years
    csp_policy: str = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' wss:"
    )


class GatewaySettings(BaseSettings):
    """Gateway connection backoff and gRPC defaults."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_GATEWAY_")

    backoff_min: float = 5.0
    backoff_max: float = 60.0
    backoff_factor: float = 2.0
    grpc_timeout: float = 30.0


class OperationsSettings(BaseSettings):
    """Long-running operation tracking tuning."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_OPS_")

    max_result_bytes: int = 65_536
    running_ttl: float = 600.0
    retention_days: int = 30
    field_truncation_chars: int = 8000
    max_list_limit: int = 200


class AuditSettings(BaseSettings):
    """Audit log retention and export."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_AUDIT_")

    retention_days: int = 90
    export_limit: int = 10_000


class WebhookSettings(BaseSettings):
    """Webhook delivery tuning."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_WEBHOOK_")

    delivery_timeout: float = 10.0
    retry_delays: list[int] = Field(default=[5, 30, 120])
    delivery_max_age_days: int = 7


class BackgroundSettings(BaseSettings):
    """Background task intervals (seconds) and backoff."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_BG_")

    cleanup_interval: int = 600
    cleanup_max_interval: int = 900
    cleanup_backoff_threshold: int = 10
    health_interval: int = 30
    health_max_interval: int = 300
    health_backoff_threshold: int = 10


class LocalGatewaySettings(BaseSettings):
    """Local gateway Docker lifecycle management."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_LOCAL_GW_")

    startup_retries: int = 10
    startup_sleep: float = 2.0
    openshell_timeout: float = 600.0
    docker_timeout: float = 30.0
    starting_port: int = 8080


class WebSocketSettings(BaseSettings):
    """WebSocket event streaming."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_WS_")

    queue_maxsize: int = 1000
    queue_get_timeout: float = 1.0
    heartbeat_interval: float = 15.0
    backpressure_drop_limit: int = 50


class SandboxSettings(BaseSettings):
    """Sandbox route defaults."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_SANDBOX_")

    ready_timeout: float = 180.0


class LimitSettings(BaseSettings):
    """Input size and validation limits."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_LIMIT_")

    max_cert_bytes: int = 65_536
    max_metadata_json_bytes: int = 16_384
    max_description_len: int = 1000
    max_labels: int = 20
    max_label_value_len: int = 253


# ─── Root settings ────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    """Root settings aggregating all sub-models.

    Each nested model is constructed via ``default_factory`` so that
    environment variables are read at instantiation time, not at class
    definition / import time.
    """

    server: ServerSettings = Field(default_factory=ServerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    ops: OperationsSettings = Field(default_factory=OperationsSettings)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    webhooks: WebhookSettings = Field(default_factory=WebhookSettings)
    background: BackgroundSettings = Field(default_factory=BackgroundSettings)
    local_gw: LocalGatewaySettings = Field(default_factory=LocalGatewaySettings)
    websocket: WebSocketSettings = Field(default_factory=WebSocketSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    limits: LimitSettings = Field(default_factory=LimitSettings)


# ─── Singleton ────────────────────────────────────────────────────────────────

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the application settings singleton.

    On first call, reads all ``SHOREGUARD_*`` environment variables.
    Subsequent calls return the cached instance.
    """
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(settings: Settings) -> None:
    """Replace the settings singleton (for CLI overrides and tests)."""
    global _settings  # noqa: PLW0603
    _settings = settings


def reset_settings() -> None:
    """Clear the cached singleton so the next ``get_settings()`` re-reads env."""
    global _settings  # noqa: PLW0603
    _settings = None
