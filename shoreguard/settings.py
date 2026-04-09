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

import logging
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# ─── Sub-models ───────────────────────────────────────────────────────────────


class ServerSettings(BaseSettings):
    """Server bind address, logging, and runtime flags."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_")

    host: str = Field(default="0.0.0.0", description="Bind address for the HTTP server")
    port: int = Field(default=8888, description="TCP port for the HTTP server")
    log_level: str = Field(
        default="info",
        description="Log level: critical|error|warning|info|debug|trace",
    )
    log_format: str = Field(
        default="text",
        description="Log output format — 'text' for humans, 'json' for aggregators",
    )
    reload: bool = Field(
        default=True,
        description="Auto-reload on code changes (dev only)",
    )
    database_url: str | None = Field(
        default=None,
        description="SQLAlchemy database URL (sqlite:/// or postgresql://). "
        "Unset falls back to sqlite in the XDG config dir.",
    )
    local_mode: bool = Field(
        default=False,
        description="Allow private-IP targets in SSRF checks (local gateway dev)",
    )
    graceful_shutdown_timeout: int = Field(
        default=15,
        description="Seconds uvicorn waits for in-flight requests on SIGTERM",
    )
    gzip_minimum_size: int = Field(
        default=1000,
        description="Minimum response body size in bytes before gzip compression kicks in",
    )
    readyz_timeout: float = Field(
        default=5.0,
        description="Timeout in seconds for /readyz dependency probes",
    )


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection pool and timeout settings.

    Only applied when the database URL is not SQLite.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_DB_")

    pool_size: int = Field(default=5, description="SQLAlchemy connection pool size")
    max_overflow: int = Field(
        default=10,
        description="Additional pool connections allowed above pool_size",
    )
    pool_timeout: int = Field(
        default=30,
        description="Seconds to wait for a pool connection before failing",
    )
    pool_recycle: int = Field(
        default=1800,
        description="Seconds after which connections are recycled",
    )
    statement_timeout_ms: int = Field(
        default=30000,
        description="PostgreSQL statement_timeout in ms (applied per connection)",
    )
    startup_retry_attempts: int = Field(
        default=10,
        description="Number of times init_db() retries Alembic upgrade on OperationalError",
    )
    startup_retry_delay: float = Field(
        default=2.0,
        description="Initial backoff in seconds between DB retry attempts",
    )
    startup_retry_max_delay: float = Field(
        default=30.0,
        description="Maximum backoff cap in seconds between DB retry attempts",
    )


class AuthSettings(BaseSettings):
    """Authentication, sessions, and registration."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_")

    no_auth: bool = Field(
        default=False,
        description="Disable authentication entirely (development only)",
    )
    secret_key: str | None = Field(
        default=None,
        description="HMAC secret for sessions and signed cookies. "
        "Unset falls back to on-disk .secret_key — set explicitly for multi-replica.",
    )
    allow_registration: bool = Field(
        default=False,
        description="Allow unauthenticated self-signup via /register",
    )
    admin_password: str | None = Field(
        default=None,
        description="Bootstrap admin password used on first startup if no users exist",
    )
    cookie_name: str = Field(default="sg_session", description="Session cookie name")
    session_max_age: int = Field(
        default=86400 * 7,
        description="Session cookie lifetime in seconds (default: 7 days)",
    )
    invite_max_age: int = Field(
        default=86400 * 7,
        description="Invite token validity in seconds (default: 7 days)",
    )
    password_min_length: int = Field(
        default=8,
        description="Minimum password length for user registration",
    )
    password_require_complexity: bool = Field(
        default=False,
        description="Require mixed-case, digit, and symbol in passwords",
    )
    login_rate_limit_attempts: int = Field(
        default=10,
        description="Max failed login attempts per IP before rate limit kicks in",
    )
    login_rate_limit_window: int = Field(
        default=300,
        description="Login rate-limit sliding window in seconds",
    )
    login_rate_limit_lockout: int = Field(
        default=900,
        description="Login rate-limit lockout duration in seconds",
    )
    account_lockout_attempts: int = Field(
        default=5,
        description="Max failed logins per account before lockout",
    )
    account_lockout_duration: int = Field(
        default=900,
        description="Account lockout duration in seconds after threshold",
    )
    write_rate_limit_attempts: int = Field(
        default=30,
        description="Max write requests per IP before rate limit kicks in",
    )
    write_rate_limit_window: int = Field(
        default=60,
        description="Write rate-limit sliding window in seconds",
    )
    write_rate_limit_lockout: int = Field(
        default=120,
        description="Write rate-limit lockout duration in seconds",
    )
    global_rate_limit_attempts: int = Field(
        default=300,
        description="Global per-IP rate limit (DDoS guardrail)",
    )
    global_rate_limit_window: int = Field(
        default=60,
        description="Global rate-limit sliding window in seconds",
    )
    global_rate_limit_lockout: int = Field(
        default=60,
        description="Global rate-limit lockout duration in seconds",
    )
    metrics_public: bool = Field(
        default=False,
        description="Expose /metrics without authentication (default: admin-only)",
    )
    hsts_enabled: bool = Field(
        default=False,
        description="Emit Strict-Transport-Security header (enable behind HTTPS proxy)",
    )
    hsts_max_age: int = Field(
        default=63072000,
        description="HSTS max-age in seconds (default: 2 years)",
    )
    csp_policy: str = Field(
        default=(
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; connect-src 'self' wss:"
        ),
        description="Content-Security-Policy header value",
    )


class GatewaySettings(BaseSettings):
    """Gateway connection backoff and gRPC defaults."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_GATEWAY_")

    backoff_min: float = Field(default=5.0, description="Initial reconnect backoff in seconds")
    backoff_max: float = Field(default=60.0, description="Maximum reconnect backoff in seconds")
    backoff_factor: float = Field(
        default=2.0, description="Exponential backoff multiplier between attempts"
    )
    grpc_timeout: float = Field(
        default=30.0, description="Default timeout for gRPC calls to gateways"
    )


class OperationsSettings(BaseSettings):
    """Long-running operation tracking tuning."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_OPS_")

    max_result_bytes: int = Field(
        default=65_536,
        description="Maximum bytes of operation result stored in DB (larger truncated)",
    )
    running_ttl: float = Field(
        default=600.0,
        description="Seconds a running operation can go without a heartbeat before timeout",
    )
    retention_days: int = Field(
        default=30,
        description="Days to retain completed operations before cleanup",
    )
    field_truncation_chars: int = Field(
        default=8000,
        description="Max characters per text field before truncation in operation records",
    )
    max_list_limit: int = Field(
        default=200,
        description="Maximum page size for /operations list queries",
    )


class AuditSettings(BaseSettings):
    """Audit log retention and export."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_AUDIT_")

    retention_days: int = Field(
        default=90, description="Days to retain audit log entries before cleanup"
    )
    export_limit: int = Field(
        default=10_000,
        description="Maximum rows returned by /audit/export in a single call",
    )


class WebhookSettings(BaseSettings):
    """Webhook delivery tuning."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_WEBHOOK_")

    delivery_timeout: float = Field(
        default=10.0, description="HTTP request timeout for webhook delivery in seconds"
    )
    retry_delays: list[int] = Field(
        default=[5, 30, 120],
        description="Retry delays in seconds between failed webhook delivery attempts",
    )
    delivery_max_age_days: int = Field(
        default=7,
        description="Days to retain webhook delivery records before cleanup",
    )


class BackgroundSettings(BaseSettings):
    """Background task intervals (seconds) and backoff."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_BG_")

    cleanup_interval: int = Field(
        default=600,
        description="Seconds between operation/audit cleanup passes",
    )
    cleanup_max_interval: int = Field(
        default=900,
        description="Maximum backoff cap for cleanup task after failures",
    )
    cleanup_backoff_threshold: int = Field(
        default=10,
        description="Consecutive cleanup failures before entering backoff mode",
    )
    health_interval: int = Field(
        default=30,
        description="Seconds between gateway health probe cycles",
    )
    health_max_interval: int = Field(
        default=300,
        description="Maximum backoff cap for health monitor after failures",
    )
    health_backoff_threshold: int = Field(
        default=10,
        description="Consecutive health probe failures before entering backoff mode",
    )


class LocalGatewaySettings(BaseSettings):
    """Local gateway Docker lifecycle management."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_LOCAL_GW_")

    startup_retries: int = Field(
        default=10,
        description="Times to retry probing a local gateway container during startup",
    )
    startup_sleep: float = Field(
        default=2.0, description="Seconds to sleep between startup probe retries"
    )
    openshell_timeout: float = Field(
        default=600.0,
        description="Timeout in seconds for openshell subprocess calls",
    )
    docker_timeout: float = Field(
        default=30.0,
        description="Timeout in seconds for docker subprocess calls (start, stop, inspect)",
    )
    starting_port: int = Field(
        default=8080, description="First port assigned to locally-spawned gateways"
    )


class WebSocketSettings(BaseSettings):
    """WebSocket event streaming."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_WS_")

    queue_maxsize: int = Field(
        default=1000,
        description="Maximum number of buffered events per WebSocket client",
    )
    queue_get_timeout: float = Field(
        default=1.0,
        description="Seconds to wait for an event before sending a heartbeat",
    )
    heartbeat_interval: float = Field(
        default=15.0,
        description="Seconds between WebSocket heartbeat frames",
    )
    backpressure_drop_limit: int = Field(
        default=50,
        description="Events dropped before a slow client is disconnected",
    )


class SandboxSettings(BaseSettings):
    """Sandbox route defaults."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_SANDBOX_")

    ready_timeout: float = Field(
        default=180.0,
        description="Seconds to wait for a sandbox to become ready before failing",
    )


class OIDCSettings(BaseSettings):
    """OpenID Connect provider configuration.

    Providers are configured via a JSON array in ``SHOREGUARD_OIDC_PROVIDERS_JSON``.
    Each entry needs ``name``, ``issuer``, ``client_id``, ``client_secret``,
    and optionally ``display_name``, ``scopes``, and ``role_mapping``.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_OIDC_")

    providers_json: str = Field(
        default="[]",
        description="JSON array of OIDC provider configs (name, issuer, client_id, ...)",
    )
    default_role: str = Field(
        default="viewer",
        description="Role assigned to OIDC users whose claims do not match any mapping",
    )
    state_max_age: int = Field(
        default=300,
        description="Seconds an OIDC state cookie remains valid after authorize redirect",
    )


class CORSSettings(BaseSettings):
    """Cross-Origin Resource Sharing policy.

    Disabled by default (empty ``allow_origins``). Set
    ``SHOREGUARD_CORS_ALLOW_ORIGINS`` to a comma-separated list of exact
    origins (e.g. ``https://app.example.com,https://admin.example.com``)
    to enable CORS for a browser-based frontend on a different origin.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_CORS_")

    allow_origins: list[str] = Field(
        default_factory=list,
        description="Exact origins permitted by CORS (comma-separated via env var)",
    )
    allow_credentials: bool = Field(
        default=True,
        description="Allow cookies/authorization headers in CORS requests",
    )
    allow_methods: list[str] = Field(
        default_factory=lambda: ["*"],
        description="HTTP methods allowed by CORS (default: all)",
    )
    allow_headers: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Request headers allowed by CORS (default: all)",
    )
    max_age: int = Field(
        default=600,
        description="CORS preflight cache duration in seconds",
    )


class LimitSettings(BaseSettings):
    """Input size and validation limits."""

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_LIMIT_")

    max_cert_bytes: int = Field(default=65_536, description="Maximum PEM certificate size in bytes")
    max_metadata_json_bytes: int = Field(
        default=16_384, description="Maximum metadata JSON payload size in bytes"
    )
    max_description_len: int = Field(
        default=1000, description="Maximum free-text description length"
    )
    max_labels: int = Field(default=20, description="Maximum label entries per resource")
    max_label_value_len: int = Field(
        default=253, description="Maximum label value length (DNS-style)"
    )
    max_name_len: int = Field(default=253, description="Maximum resource name length (DNS-style)")
    max_url_len: int = Field(default=2048, description="Maximum URL length in any field")
    max_api_key_len: int = Field(default=512, description="Maximum API key token length")
    max_event_types: int = Field(
        default=50, description="Maximum event types per webhook subscription"
    )
    max_event_type_len: int = Field(default=100, description="Maximum event type string length")
    max_env_vars: int = Field(
        default=100, description="Maximum environment variables per sandbox/command"
    )
    max_env_key_len: int = Field(default=256, description="Maximum env var key length")
    max_env_value_len: int = Field(default=8192, description="Maximum env var value length")
    max_config_entries: int = Field(
        default=50, description="Maximum config map entries per resource"
    )
    max_config_value_len: int = Field(default=8192, description="Maximum config map value length")
    max_command_len: int = Field(default=65_536, description="Maximum command-line string length")
    max_reason_len: int = Field(default=1000, description="Maximum audit reason text length")
    max_timeout_secs: int = Field(
        default=3600, description="Maximum per-operation timeout requestable by API"
    )
    max_image_len: int = Field(default=512, description="Maximum container image reference length")
    max_password_len: int = Field(
        default=128, description="Maximum password length accepted (bcrypt 72-byte limit)"
    )
    max_request_body_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum HTTP request body size in bytes (default: 10 MiB)",
    )


# ─── Root settings ────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    """Root settings aggregating all sub-models.

    Each nested model is constructed via ``default_factory`` so that
    environment variables are read at instantiation time, not at class
    definition / import time.
    """

    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
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
    oidc: OIDCSettings = Field(default_factory=OIDCSettings)
    cors: CORSSettings = Field(default_factory=CORSSettings)

    def _is_prod_like(self) -> bool:
        """Heuristic for whether the current config looks like a production deployment.

        Used by :meth:`check_production_readiness` to gate warnings that
        would be noise in local development.  Returns ``True`` when all
        three signals indicate non-dev use:

        * ``local_mode`` is off (SSRF allow-list for private IPs disabled)
        * ``no_auth`` is off (auth is actually required)
        * ``host`` is not bound to the loopback interface
        """
        return (
            not self.server.local_mode
            and not self.auth.no_auth
            and self.server.host not in {"127.0.0.1", "localhost", "::1"}
        )

    def check_production_readiness(self) -> list[str]:
        """Validate production-critical configuration and log warnings.

        Runs at application startup to surface insecure or likely-wrong
        configuration before the server begins serving traffic.  Warnings
        are prefixed with a severity tag (``ERROR:`` for security-critical
        issues, ``WARN:`` for likely mistakes) and logged via ``logger.warning``.

        Returns:
            list[str]: Human-readable warning messages (empty if all OK).
        """
        warnings: list[str] = []
        prod_like = self._is_prod_like()

        # ── Basics (always checked) ─────────────────────────────────────
        valid_levels = {"critical", "error", "warning", "info", "debug", "trace"}
        if self.server.log_level.lower() not in valid_levels:
            warnings.append(
                f"WARN: server.log_level={self.server.log_level!r} is not one of "
                f"{sorted(valid_levels)}"
            )

        if not (0 < self.server.port < 65536):
            warnings.append(f"WARN: server.port={self.server.port} is out of range")

        if not self.auth.no_auth:
            if self.auth.secret_key is None:
                warnings.append(
                    "ERROR: auth.secret_key is unset — falling back to on-disk .secret_key. "
                    "For multi-replica or container deployments set SHOREGUARD_SECRET_KEY "
                    "to a stable value."
                )
            elif len(self.auth.secret_key) < 32:
                warnings.append(
                    f"ERROR: auth.secret_key is only {len(self.auth.secret_key)} chars — "
                    "use at least 32 random characters"
                )

            if self.auth.admin_password is not None and len(self.auth.admin_password) < 12:
                warnings.append(
                    "WARN: auth.admin_password is shorter than 12 chars — pick a longer password"
                )

        if self.cors.allow_credentials and "*" in self.cors.allow_origins:
            warnings.append(
                "ERROR: cors.allow_origins contains '*' together with allow_credentials=True — "
                "browsers will reject credentialed requests; list exact origins instead"
            )

        if self.database.pool_size < 1:
            warnings.append(f"WARN: database.pool_size={self.database.pool_size} must be >= 1")

        # ── CSP unsafe-* (always checked — never acceptable in prod) ────
        if "'unsafe-" in self.auth.csp_policy:
            warnings.append(
                "ERROR: auth.csp_policy contains 'unsafe-*' directives "
                "(unsafe-inline / unsafe-eval) — XSS protection is degraded"
            )

        # ── Prod-like gated checks ──────────────────────────────────────
        if prod_like:
            if not self.auth.hsts_enabled:
                warnings.append(
                    "WARN: auth.hsts_enabled=false in a production-like deployment — "
                    "set SHOREGUARD_HSTS_ENABLED=true when serving behind an HTTPS proxy"
                )

            if self.auth.allow_registration:
                warnings.append(
                    "ERROR: auth.allow_registration=true in a production-like deployment — "
                    "enables unrestricted self-signup"
                )

            replicas = os.environ.get("SHOREGUARD_REPLICAS", "1")
            try:
                replica_count = int(replicas)
            except ValueError:
                replica_count = 1
            if replica_count > 1:
                warnings.append(
                    f"WARN: SHOREGUARD_REPLICAS={replica_count} but the rate limiters are "
                    "in-process only — limits do not coordinate across replicas "
                    "(Redis-backed limiter is a v1.x item)"
                )

            from shoreguard.config import default_database_url

            db_url = self.server.database_url or default_database_url()
            if db_url.startswith("sqlite"):
                warnings.append(
                    "ERROR: database_url is SQLite in a production-like deployment — "
                    "use PostgreSQL for concurrent access and durability"
                )

            if self.server.log_format != "json":
                warnings.append(
                    f"WARN: server.log_format={self.server.log_format!r} in a production-like "
                    "deployment — set SHOREGUARD_LOG_FORMAT=json for machine-parseable logs"
                )

        for msg in warnings:
            logger.warning("Config check: %s", msg)

        return warnings


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
