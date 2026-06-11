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

import ipaddress
import logging
import os

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Bind addresses that only expose the server on the local machine.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _validate_cidr_list(value: str, env_var: str) -> str:
    """Parse each entry of a comma-separated CIDR list so misconfigurations fail fast.

    Keeps the original string representation but rejects the whole setting
    if any entry is unparsable — the alternative (silent drop) would mean
    an operator who typo'd a CIDR would get the opposite of the security
    guarantee they asked for.

    Args:
        value: Raw comma-separated CIDR string from the environment.
        env_var: Environment variable name, used in the error message.

    Returns:
        str: The unchanged input when every entry is parseable.

    Raises:
        ValueError: If any comma-separated entry fails to parse as an
            IPv4/IPv6 network literal.
    """
    for entry in (p.strip() for p in value.split(",") if p.strip()):
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError as exc:
            msg = f"invalid CIDR in {env_var}: {entry!r} ({exc})"
            raise ValueError(msg) from exc
    return value


# ─── Sub-models ───────────────────────────────────────────────────────────────


class ServerSettings(BaseSettings):
    """Server bind address, logging, and runtime flags.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        host (str): Bind address for the HTTP server.
        port (int): TCP port for the HTTP server.
        public_url (str | None): Externally reachable base URL used to build
            absolute links in notifications (one-tap approvals).
        log_level (str): Log level: critical|error|warning|info|debug|trace.
        log_format (str): Log output format — 'text' for humans, 'json' for aggregators.
        reload (bool): Auto-reload on code changes (dev only).
        database_url (str | None): SQLAlchemy database URL (sqlite:/// or postgresql://).
            Unset falls back to sqlite in the XDG config dir.
        local_mode (bool): Allow private-IP targets in SSRF checks (local gateway dev).
        graceful_shutdown_timeout (int): Seconds uvicorn waits for in-flight requests on SIGTERM.
        gzip_minimum_size (int): Minimum response body size in bytes before gzip kicks in.
        readyz_timeout (float): Timeout in seconds for /readyz dependency probes.
        forwarded_allow_ips (str): Comma-separated IPs (or "*") whose X-Forwarded-* headers
            uvicorn trusts. Default "127.0.0.1" is wrong behind a k8s Ingress — set to "*"
            (or the ingress controller's pod CIDR) when serving behind a TLS-terminating proxy.
        always_blocked_ips (str): Comma-separated IPs or CIDR ranges that are always
            blocked as SSRF targets regardless of local_mode. Parsed once at startup;
            an invalid entry hard-fails boot.
        ssrf_allowed_ips (str): Comma-separated IPs or CIDR ranges exempted from the
            private/loopback SSRF rejection. Matched against the resolved address;
            entries in always_blocked_ips take precedence. Parsed once at startup;
            an invalid entry hard-fails boot.
        unsafe_lan (bool): Allow serving without authentication (no_auth) on a
            non-loopback bind address. Off by default — an unauthenticated UI on
            a network interface gives everyone on that network admin access.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_")

    host: str = Field(
        default="0.0.0.0",  # nosec B104 # Intentional default for containerised deploys behind a reverse proxy
        description="Bind address for the HTTP server",
    )
    port: int = Field(default=8888, description="TCP port for the HTTP server")
    public_url: str | None = Field(
        default=None,
        description="Externally reachable base URL of this ShoreGuard "
        "(e.g. https://spark.tail1234.ts.net). Used to build absolute links "
        "in notifications; required for one-tap approval links.",
    )
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
    forwarded_allow_ips: str = Field(
        default="127.0.0.1",
        description="Comma-separated IPs (or '*') whose X-Forwarded-* headers uvicorn "
        "trusts. Set to '*' when serving behind a k8s Ingress — the default only "
        "trusts loopback, which means TLS-terminating proxies are ignored.",
    )
    always_blocked_ips: str = Field(
        default="",
        description="Comma-separated IPs or CIDR ranges that are always blocked as SSRF "
        "targets regardless of local_mode. Mirrors upstream OpenShell #814. Parsed once "
        "at startup; an invalid entry hard-fails boot.",
    )
    ssrf_allowed_ips: str = Field(
        default="",
        description="Comma-separated IPs or CIDR ranges exempted from the private/loopback "
        "SSRF rejection — e.g. a homelab OIDC provider or webhook target on a LAN address. "
        "Matched against the resolved address, so hostnames are exempt only if they resolve "
        "into an allowlisted range. SHOREGUARD_ALWAYS_BLOCKED_IPS takes precedence. Parsed "
        "once at startup; an invalid entry hard-fails boot.",
    )
    unsafe_lan: bool = Field(
        default=False,
        description="Allow serving without authentication (SHOREGUARD_NO_AUTH) on a "
        "non-loopback bind address. Off by default — an unauthenticated UI on a network "
        "interface gives everyone on that network admin access.",
    )

    @field_validator("always_blocked_ips")
    @classmethod
    def _validate_always_blocked_ips(cls, value: str) -> str:
        """Validate the always-blocked CIDR list at load time.

        Propagates ``ValueError`` from :func:`_validate_cidr_list` for
        unparsable entries.

        Args:
            value: Raw comma-separated CIDR string from the environment.

        Returns:
            str: The unchanged input when every entry is parseable.
        """
        return _validate_cidr_list(value, "SHOREGUARD_ALWAYS_BLOCKED_IPS")

    @field_validator("ssrf_allowed_ips")
    @classmethod
    def _validate_ssrf_allowed_ips(cls, value: str) -> str:
        """Validate the SSRF allowlist CIDR list at load time.

        Propagates ``ValueError`` from :func:`_validate_cidr_list` for
        unparsable entries.

        Args:
            value: Raw comma-separated CIDR string from the environment.

        Returns:
            str: The unchanged input when every entry is parseable.
        """
        return _validate_cidr_list(value, "SHOREGUARD_SSRF_ALLOWED_IPS")


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection pool and timeout settings.

    Only applied when the database URL is not SQLite.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        pool_size (int): SQLAlchemy connection pool size.
        max_overflow (int): Additional pool connections allowed above pool_size.
        pool_timeout (int): Seconds to wait for a pool connection before failing.
        pool_recycle (int): Seconds after which connections are recycled.
        statement_timeout_ms (int): PostgreSQL statement_timeout in ms (per connection).
        startup_retry_attempts (int): Retries init_db() does on OperationalError.
        startup_retry_delay (float): Initial backoff in seconds between DB retry attempts.
        startup_retry_max_delay (float): Maximum backoff cap in seconds between DB retries.
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
    """Authentication, sessions, and registration.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        no_auth (bool): Disable authentication entirely (development only).
        single_user (bool): Single-user mode — one admin account synced from
            SHOREGUARD_ADMIN_PASSWORD on every startup.
        tailscale_identity (bool): Trust Tailscale Serve identity headers from
            a loopback proxy as authentication (login must match a user email).
        secret_key (str | None): HMAC secret for sessions and signed cookies. Unset falls back
            to on-disk .secret_key — set explicitly for multi-replica.
        allow_registration (bool): Allow unauthenticated self-signup via /register.
        admin_password (str | None): Bootstrap admin password used on first startup if no
            users exist.
        cookie_name (str): Session cookie name.
        session_max_age (int): Session cookie lifetime in seconds (default: 7 days).
        invite_max_age (int): Invite token validity in seconds (default: 7 days).
        password_min_length (int): Minimum password length for user registration.
        password_require_complexity (bool): Require mixed-case, digit, and symbol in passwords.
        login_rate_limit_attempts (int): Max failed logins per IP before rate limit.
        login_rate_limit_window (int): Login rate-limit sliding window in seconds.
        login_rate_limit_lockout (int): Login rate-limit lockout duration in seconds.
        account_lockout_attempts (int): Max failed logins per account before lockout.
        account_lockout_duration (int): Account lockout duration in seconds after threshold.
        write_rate_limit_attempts (int): Max write requests per IP before rate limit.
        write_rate_limit_window (int): Write rate-limit sliding window in seconds.
        write_rate_limit_lockout (int): Write rate-limit lockout duration in seconds.
        global_rate_limit_attempts (int): Global per-IP rate limit (DDoS guardrail).
        global_rate_limit_window (int): Global rate-limit sliding window in seconds.
        global_rate_limit_lockout (int): Global rate-limit lockout duration in seconds.
        metrics_public (bool): Expose /metrics without authentication (default: admin-only).
        hsts_enabled (bool): Emit Strict-Transport-Security header (enable behind HTTPS).
        hsts_max_age (int): HSTS max-age in seconds (default: 2 years).
        csp_policy (str): Content-Security-Policy header value (used when csp_strict=False).
        csp_strict (bool): Enforce strict CSP with per-request nonce and no 'unsafe-*'.
            Requires all inline scripts to be nonce-gated and the Alpine.js CSP build.
            Default off until the frontend refactor (M2–M4) is complete.
        csp_policy_strict (str): CSP template used when csp_strict=True. Must contain a
            '{nonce}' placeholder that is replaced per-request.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_")

    no_auth: bool = Field(
        default=False,
        description="Disable authentication entirely (development only)",
    )
    single_user: bool = Field(
        default=False,
        description="Single-user mode: one admin account whose password is "
        "SHOREGUARD_ADMIN_PASSWORD, kept in sync on every startup. The "
        "homelab middle ground between --no-auth and full RBAC.",
    )
    tailscale_identity: bool = Field(
        default=False,
        description="Trust Tailscale Serve identity headers "
        "(Tailscale-User-Login) from a loopback proxy as authentication. "
        "The login must match an existing user's email. Only honoured for "
        "connections from 127.0.0.1/::1 — i.e. `tailscale serve` in front "
        "of a loopback-bound ShoreGuard.",
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
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; connect-src 'self' wss:"
        ),
        description="Content-Security-Policy header value (used when csp_strict=False)",
    )
    csp_strict: bool = Field(
        default=True,
        description=(
            "Enforce strict CSP with per-request nonce, no 'unsafe-inline', and "
            "frame-ancestors 'none'. Default as of v0.27.0 — blocks inline scripts, "
            "inline event handlers, and inline styles. Since the Preact islands "
            "rewrite removed Alpine.js, 'unsafe-eval' is no longer needed. "
            "Set SHOREGUARD_CSP_STRICT=false to fall back to the legacy "
            "'unsafe-inline' policy in `csp_policy`."
        ),
    )
    csp_policy_strict: str = Field(
        default=(
            "default-src 'self'; "
            "script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            "style-src 'self' https://cdn.jsdelivr.net; "
            "style-src-attr 'unsafe-inline'; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; connect-src 'self' wss:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        ),
        description=(
            "CSP template used when csp_strict=True. Must contain a '{nonce}' "
            "placeholder that is replaced per-request."
        ),
    )


class GatewaySettings(BaseSettings):
    """Gateway connection backoff and gRPC defaults.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        backoff_min (float): Initial reconnect backoff in seconds.
        backoff_max (float): Maximum reconnect backoff in seconds.
        backoff_factor (float): Exponential backoff multiplier between attempts.
        grpc_timeout (float): Default timeout for gRPC calls to gateways.
        grpc_retry_max_attempts (int): Maximum attempts per logical RPC.
        grpc_retry_initial_backoff (float): Initial backoff between retries in seconds.
        grpc_retry_max_backoff (float): Maximum backoff between retries in seconds.
        grpc_retry_deadline (float): Total wall-clock budget including retries.
        require_mtls (bool): Reject plaintext gateway channels when ``True``.
        cert_expiry_warn_days (int): Warn when any gateway cert expires within
            this many days.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_GATEWAY_")

    backoff_min: float = Field(default=5.0, description="Initial reconnect backoff in seconds")
    backoff_max: float = Field(default=60.0, description="Maximum reconnect backoff in seconds")
    backoff_factor: float = Field(
        default=2.0, description="Exponential backoff multiplier between attempts"
    )
    grpc_timeout: float = Field(
        default=30.0, description="Default timeout for gRPC calls to gateways"
    )
    grpc_retry_max_attempts: int = Field(
        default=4,
        description="Maximum number of attempts (including the first) for a retryable gRPC call",
    )
    grpc_retry_initial_backoff: float = Field(
        default=0.25,
        description="Initial exponential backoff between retries in seconds",
    )
    grpc_retry_max_backoff: float = Field(
        default=4.0,
        description="Maximum exponential backoff between retries in seconds",
    )
    grpc_retry_deadline: float = Field(
        default=60.0,
        description=(
            "Total wall-clock budget in seconds for a single logical RPC including all "
            "retries. Retries will not exceed this deadline."
        ),
    )
    require_mtls: bool = Field(
        default=True,
        description=(
            "Reject plaintext gRPC channels to gateways. Disable only for local "
            "development against an insecure gateway."
        ),
    )
    cert_expiry_warn_days: int = Field(
        default=14,
        description=(
            "Warn (but do not reject) when a gateway certificate expires within this "
            "many days. A structured log warning is emitted per affected channel."
        ),
    )


class OperationsSettings(BaseSettings):
    """Long-running operation tracking tuning.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        max_result_bytes (int): Max bytes of operation result stored in DB (larger truncated).
        running_ttl (float): Seconds a running operation can go without heartbeat before
            timeout.
        retention_days (int): Days to retain completed operations before cleanup.
        field_truncation_chars (int): Max characters per text field before truncation in
            operation records.
        max_list_limit (int): Maximum page size for /operations list queries.
    """

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
    """Audit log retention and export.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        retention_days (int): Days to retain audit log entries before cleanup.
        export_limit (int): Maximum rows returned by /audit/export in a single call.
        export_stdout_json (bool): Emit each audit entry as a JSON line on stdout.
        export_syslog_enabled (bool): Ship each audit entry to a remote syslog receiver.
        export_syslog_host (str): Syslog server host when export_syslog_enabled=true.
        export_syslog_port (int): Syslog server port when export_syslog_enabled=true.
        export_syslog_facility (str): Syslog facility name (user, local0..local7, ...).
        export_webhook_enabled (bool): Bridge audit entries into the webhook pipeline.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_AUDIT_")

    retention_days: int = Field(
        default=90, description="Days to retain audit log entries before cleanup"
    )
    export_limit: int = Field(
        default=10_000,
        description="Maximum rows returned by /audit/export in a single call",
    )
    export_stdout_json: bool = Field(
        default=False,
        description="Emit each audit entry as a JSON line on stdout (Loki/Vector lane)",
    )
    export_syslog_enabled: bool = Field(
        default=False,
        description="Ship each audit entry to a remote syslog receiver as JSON body",
    )
    export_syslog_host: str = Field(
        default="localhost",
        description="Syslog server host when export_syslog_enabled=true",
    )
    export_syslog_port: int = Field(
        default=514,
        description="Syslog server port when export_syslog_enabled=true",
    )
    export_syslog_facility: str = Field(
        default="user",
        description="Syslog facility name (user, local0..local7, daemon, ...)",
    )
    export_webhook_enabled: bool = Field(
        default=False,
        description=(
            "Bridge audit entries into the existing webhook pipeline as "
            "'audit.entry' events; individual targets are configured per Webhook record"
        ),
    )


class TracingSettings(BaseSettings):
    """OpenTelemetry trace context propagation for the routed-inference path.

    When ``enabled`` is true, incoming HTTP requests and outgoing gRPC calls
    to OpenShell gateways are instrumented so that a W3C ``traceparent`` header
    flows end-to-end. When an ``otlp_endpoint`` is set, spans are shipped via
    OTLP/HTTP; otherwise a console exporter is used so locally-running
    operators can verify propagation without standing up a collector.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        enabled (bool): Master switch for FastAPI + gRPC auto-instrumentation.
        service_name (str): Value used for ``service.name`` resource attribute.
        otlp_endpoint (str | None): OTLP/HTTP traces endpoint (e.g.
            ``http://localhost:4318/v1/traces``). None = console exporter.
        sample_ratio (float): Head-based sampling ratio in ``[0.0, 1.0]``.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_TRACING_")

    enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry auto-instrumentation for FastAPI and gRPC client",
    )
    service_name: str = Field(
        default="shoreguard",
        description="service.name resource attribute attached to every span",
    )
    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP/HTTP traces endpoint URL; if unset, spans go to stdout console exporter",
    )
    sample_ratio: float = Field(
        default=1.0,
        description="Head-based sampling ratio between 0.0 (off) and 1.0 (all)",
    )


class WebhookSettings(BaseSettings):
    """Webhook delivery tuning.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        delivery_timeout (float): HTTP request timeout for webhook delivery in seconds.
        retry_delays (list[int]): Retry delays in seconds between failed delivery attempts.
        delivery_max_age_days (int): Days to retain webhook delivery records before cleanup.
        one_tap_approvals (bool): Attach signed one-tap approve/reject links to
            approval webhook events (requires SHOREGUARD_PUBLIC_URL).
        one_tap_ttl (int): Validity window of one-tap links in seconds.
    """

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
    one_tap_approvals: bool = Field(
        default=False,
        description="Attach signed one-tap approve/reject links to approval "
        "webhook events. Anyone holding such a link can cast that one vote "
        "until it expires — treat notification channels accordingly. "
        "Requires SHOREGUARD_PUBLIC_URL.",
    )
    one_tap_ttl: int = Field(
        default=3600,
        description="Validity window of one-tap approval links in seconds",
    )


class BackgroundSettings(BaseSettings):
    """Background task intervals (seconds) and backoff.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        cleanup_interval (int): Seconds between operation/audit cleanup passes.
        cleanup_max_interval (int): Maximum backoff cap for cleanup task after failures.
        cleanup_backoff_threshold (int): Consecutive cleanup failures before backoff mode.
        health_interval (int): Seconds between gateway health probe cycles.
        health_max_interval (int): Maximum backoff cap for health monitor after failures.
        health_backoff_threshold (int): Consecutive health probe failures before backoff.
    """

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


class BudgetSettings(BaseSettings):
    """Inference spend metering and per-sandbox budgets (phase 1).

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        metering_enabled (bool): Enable the log-polling usage metering task.
        interval_seconds (int): Metering poll interval.
        inference_sources (list[str]): Log ``source`` substrings counted as
            inference requests.
        log_batch_lines (int): Max log lines fetched per sandbox per poll.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_BUDGET_")

    metering_enabled: bool = Field(
        default=False,
        description="Meter per-sandbox inference usage by polling gateway "
        "logs (phase 1 — replaced by the upstream metering RPC when it "
        "lands). Required for budgets and the usage/spend views.",
    )
    interval_seconds: int = Field(
        default=60,
        ge=10,
        description="Usage metering poll interval in seconds",
    )
    inference_sources: list[str] = Field(
        default_factory=lambda: ["inference", "proxy"],
        description="Log source substrings counted as inference requests",
    )
    log_batch_lines: int = Field(
        default=2000,
        ge=100,
        description="Maximum log lines fetched per sandbox per metering poll",
    )


class DigestSettings(BaseSettings):
    """Daily activity digest ("what did my agents do while I slept?").

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        enabled (bool): Dispatch a daily ``digest.daily`` webhook event.
        hour (int): Local hour of day (0-23) after which the digest is sent.
        check_interval (int): Seconds between is-it-due checks by the
            background task.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_DIGEST_")

    enabled: bool = Field(
        default=False,
        description="Dispatch a daily digest.daily webhook event summarising "
        "the last 24h (audit activity, sandbox churn, approvals, gateway "
        "health, webhook failures)",
    )
    hour: int = Field(
        default=7,
        ge=0,
        le=23,
        description="Local hour of day after which the daily digest is sent",
    )
    check_interval: int = Field(
        default=600,
        description="Seconds between digest due-checks by the background task",
    )


class SmtpSettings(BaseSettings):
    """Server-wide SMTP defaults for the email webhook channel.

    Per-webhook ``extra_config`` values always win; these fill the gaps
    so a homelab box configures SMTP once instead of per webhook.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        host (str | None): Default SMTP relay host. When set, email
            webhooks no longer need ``smtp_host`` in their config.
        port (int): Default SMTP port.
        username (str | None): Default SMTP username.
        password (str | None): Default SMTP password (secret).
        from_addr (str): Default From address.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_SMTP_")

    host: str | None = Field(
        default=None,
        description="Default SMTP relay host for email webhooks. When set, "
        "email webhooks only need to_addrs in their extra_config; "
        "per-webhook smtp_host still overrides.",
    )
    port: int = Field(default=587, ge=1, le=65535, description="Default SMTP port")
    username: str | None = Field(default=None, description="Default SMTP username")
    password: str | None = Field(default=None, description="Default SMTP password (secret)")
    from_addr: str = Field(
        default="shoreguard@localhost",
        description="Default From address for email webhooks",
    )


class PushSettings(BaseSettings):
    """Web Push (PWA notifications) configuration.

    The VAPID keypair is generated on first use and persisted next to
    the secret key; only the contact claim is configurable.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        contact (str): VAPID ``sub`` contact claim sent to push services.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_PUSH_")

    contact: str = Field(
        default="mailto:admin@localhost",
        description="VAPID contact claim (mailto: or https:) sent to "
        "browser push services with each notification",
    )


class NodeAlertSettings(BaseSettings):
    """Threshold alerts on the host node-stats sample.

    Fires ``node.threshold_breached`` / ``node.recovered`` webhook events
    on state transitions, so a hot GPU or a filling disk reaches the
    phone instead of waiting on a dashboard glance. On GB10's unified
    memory, host memory pressure *is* GPU memory pressure.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        enabled (bool): Run the periodic threshold-evaluation task.
        interval_seconds (int): Seconds between evaluations.
        gpu_temp_c (float): GPU temperature breach threshold in °C.
        disk_used_pct (float): Root-disk usage breach threshold in percent.
        mem_used_pct (float): Memory usage breach threshold in percent.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_NODE_ALERT_")

    enabled: bool = Field(
        default=True,
        description="Evaluate host thresholds periodically and fire "
        "node.threshold_breached / node.recovered webhook events on "
        "transitions (no-op unless a webhook subscribes)",
    )
    interval_seconds: int = Field(
        default=60,
        ge=10,
        description="Seconds between host threshold evaluations",
    )
    gpu_temp_c: float = Field(
        default=85.0,
        description="GPU temperature breach threshold in degrees Celsius",
    )
    disk_used_pct: float = Field(
        default=90.0,
        description="Root-disk usage breach threshold in percent",
    )
    mem_used_pct: float = Field(
        default=95.0,
        description="Host memory usage breach threshold in percent",
    )


class CertRotationSettings(BaseSettings):
    """Proactive mTLS client-cert rotation settings.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        enabled (bool): Whether the background cert-rotation service runs.
        threshold_days (int): Rotate when a client cert has fewer than
            this many days remaining until expiry.
        poll_interval_s (int): Seconds between rotation-check passes.
        max_retries (int): Retry attempts when a rotation RPC fails
            before giving up for this poll cycle. Next poll cycle tries
            afresh.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_CERT_ROTATION_")

    enabled: bool = Field(
        default=True,
        description="Enable the background proactive cert-rotation service",
    )
    threshold_days: int = Field(
        default=7,
        description="Rotate when remaining validity drops below this many days",
    )
    poll_interval_s: int = Field(
        default=3600,
        description="Seconds between rotation-check passes across gateways",
    )
    max_retries: int = Field(
        default=3,
        description="Retry attempts per rotation before deferring to the next cycle",
    )


class LocalGatewaySettings(BaseSettings):
    """Local gateway Docker lifecycle management.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        startup_retries (int): Times to retry probing a local gateway container at startup.
        startup_sleep (float): Seconds to sleep between startup probe retries.
        openshell_timeout (float): Timeout in seconds for openshell subprocess calls.
        docker_timeout (float): Timeout in seconds for docker subprocess calls.
        starting_port (int): First port assigned to locally-spawned gateways.
    """

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
    """WebSocket event streaming.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        queue_maxsize (int): Maximum number of buffered events per WebSocket client.
        queue_get_timeout (float): Seconds to wait for an event before sending a heartbeat.
        heartbeat_interval (float): Seconds between WebSocket heartbeat frames.
        backpressure_drop_limit (int): Events dropped before a slow client is disconnected.
    """

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
    """Sandbox route defaults.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        ready_timeout (float): Seconds to wait for a sandbox to become ready before failing.
    """

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

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        providers_json (str): JSON array of OIDC provider configs.
        default_role (str): Role assigned to OIDC users whose claims do not match any mapping.
        state_max_age (int): Seconds an OIDC state cookie remains valid after authorize.
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


class ProverSettings(BaseSettings):
    """Z3 policy prover settings.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        timeout_ms (int): Z3 solver timeout per query in milliseconds.
        max_queries_per_request (int): Maximum queries per verify request.
        enabled (bool): Enable/disable the prover feature.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_PROVER_")

    timeout_ms: int = Field(
        default=5000,
        description="Z3 solver timeout per query in milliseconds",
    )
    max_queries_per_request: int = Field(
        default=10,
        description="Maximum queries per verify request",
    )
    enabled: bool = Field(
        default=True,
        description="Enable/disable the prover feature",
    )


class DriftDetectionSettings(BaseSettings):
    """Settings for the background policy drift detection loop.

    Off by default. When enabled, ShoreGuard periodically polls
    every registered sandbox's policy hash and fires a
    ``policy.drift_detected`` webhook when the hash changes between
    scans — the signal that someone edited the policy outside the
    GitOps pipeline.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        enabled (bool): Enable the drift detection background task.
        interval_seconds (int): Background re-scan interval (>= 60).
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_DRIFT_DETECTION_")

    enabled: bool = Field(
        default=False,
        description="Enable the policy drift detection background loop",
    )
    interval_seconds: int = Field(
        default=300,
        ge=60,
        description="Re-scan interval in seconds (>= 60)",
    )


class DiscoverySettings(BaseSettings):
    """Settings for the DNS SRV gateway auto-discovery loop.

    Off by default. When enabled, ShoreGuard periodically queries
    DNS for ``_openshell._tcp.<domain>`` SRV records and
    auto-registers any newly discovered endpoints that pass the
    standard endpoint validation guards.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        enabled (bool): Enable the discovery background task.
        domains (list[str]): Base domains to scan (comma-separated via env).
        interval_seconds (int): Background re-scan interval.
        default_scheme (str): Scheme assigned to auto-registered gateways.
        auto_register (bool): If false, discovery only lists endpoints.
        resolver_timeout_seconds (float): Per-query DNS timeout.
        mdns_enabled (bool): Also browse mDNS/zeroconf on the local network.
        mdns_timeout_seconds (float): mDNS browse window in seconds.
    """

    model_config = SettingsConfigDict(env_prefix="SHOREGUARD_DISCOVERY_")

    enabled: bool = Field(
        default=False,
        description="Enable the gateway discovery background loop",
    )
    domains: list[str] = Field(
        default_factory=list,
        description="Base domains to scan for `_openshell._tcp` SRV records",
    )
    interval_seconds: int = Field(
        default=300,
        ge=30,
        description="Background re-scan interval in seconds (>= 30)",
    )
    default_scheme: str = Field(
        default="grpc+tls",
        description="Connection scheme assigned to auto-registered gateways",
    )
    auto_register: bool = Field(
        default=True,
        description="If false, discovery only lists endpoints without registering",
    )
    resolver_timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        description="Per-query DNS resolver timeout in seconds",
    )
    mdns_enabled: bool = Field(
        default=False,
        description="Also browse mDNS/zeroconf (`_openshell._tcp.local.`) "
        "during discovery scans — finds gateways on the local network "
        "without any DNS server (homelab)",
    )
    mdns_timeout_seconds: float = Field(
        default=3.0,
        ge=0.5,
        description="How long an mDNS browse listens for announcements",
    )


class CORSSettings(BaseSettings):
    """Cross-Origin Resource Sharing policy.

    Disabled by default (empty ``allow_origins``). Set
    ``SHOREGUARD_CORS_ALLOW_ORIGINS`` to a comma-separated list of exact
    origins (e.g. ``https://app.example.com,https://admin.example.com``)
    to enable CORS for a browser-based frontend on a different origin.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        allow_origins (list[str]): Exact origins permitted by CORS.
        allow_credentials (bool): Allow cookies/authorization headers in CORS requests.
        allow_methods (list[str]): HTTP methods allowed by CORS (default: all).
        allow_headers (list[str]): Request headers allowed by CORS (default: all).
        max_age (int): CORS preflight cache duration in seconds.
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
    """Input size and validation limits.

    Attributes:
        model_config (SettingsConfigDict): Pydantic settings configuration.
        max_cert_bytes (int): Maximum PEM certificate size in bytes.
        max_metadata_json_bytes (int): Maximum metadata JSON payload size in bytes.
        max_description_len (int): Maximum free-text description length.
        max_labels (int): Maximum label entries per resource.
        max_label_value_len (int): Maximum label value length (DNS-style).
        max_name_len (int): Maximum resource name length (DNS-style).
        max_url_len (int): Maximum URL length in any field.
        max_api_key_len (int): Maximum API key token length.
        max_event_types (int): Maximum event types per webhook subscription.
        max_event_type_len (int): Maximum event type string length.
        max_env_vars (int): Maximum environment variables per sandbox/command.
        max_env_key_len (int): Maximum env var key length.
        max_env_value_len (int): Maximum env var value length.
        max_config_entries (int): Maximum config map entries per resource.
        max_config_value_len (int): Maximum config map value length.
        max_command_len (int): Maximum command-line string length.
        max_reason_len (int): Maximum audit reason text length.
        max_timeout_secs (int): Maximum per-operation timeout requestable by API.
        max_image_len (int): Maximum container image reference length.
        max_password_len (int): Maximum password length accepted (bcrypt 72-byte limit).
        max_request_body_bytes (int): Maximum HTTP request body size in bytes (default 10 MiB).
    """

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

    Attributes:
        server (ServerSettings): HTTP server bind/logging/runtime flags.
        database (DatabaseSettings): PostgreSQL connection pool and timeout settings.
        auth (AuthSettings): Authentication, sessions, rate limits, CSP, HSTS.
        gateway (GatewaySettings): Gateway reconnect backoff and gRPC defaults.
        ops (OperationsSettings): Long-running operation tracking tuning.
        audit (AuditSettings): Audit log retention and export limits.
        webhooks (WebhookSettings): Webhook delivery tuning.
        background (BackgroundSettings): Background task intervals and backoff.
        local_gw (LocalGatewaySettings): Local gateway Docker lifecycle management.
        websocket (WebSocketSettings): WebSocket event streaming tuning.
        sandbox (SandboxSettings): Sandbox route defaults.
        limits (LimitSettings): Input size and validation limits.
        oidc (OIDCSettings): OpenID Connect provider configuration.
        cors (CORSSettings): Cross-Origin Resource Sharing policy.
        prover (ProverSettings): Z3 policy prover settings.
        discovery (DiscoverySettings): DNS-SRV gateway auto-discovery.
        drift_detection (DriftDetectionSettings): Background policy drift detection.
        tracing (TracingSettings): OpenTelemetry trace context propagation.
        cert_rotation (CertRotationSettings): Proactive mTLS cert rotation.
        digest (DigestSettings): Daily activity digest dispatch.
        budget (BudgetSettings): Inference usage metering and budgets.
        smtp (SmtpSettings): Server-wide SMTP defaults for email webhooks.
        node_alert (NodeAlertSettings): Host threshold alert evaluation.
        push (PushSettings): Web Push (PWA notification) configuration.
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
    prover: ProverSettings = Field(default_factory=ProverSettings)
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    drift_detection: DriftDetectionSettings = Field(default_factory=DriftDetectionSettings)
    tracing: TracingSettings = Field(default_factory=TracingSettings)
    cert_rotation: CertRotationSettings = Field(default_factory=CertRotationSettings)
    digest: DigestSettings = Field(default_factory=DigestSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    smtp: SmtpSettings = Field(default_factory=SmtpSettings)
    node_alert: NodeAlertSettings = Field(default_factory=NodeAlertSettings)
    push: PushSettings = Field(default_factory=PushSettings)

    def _is_prod_like(self) -> bool:
        """Heuristic for whether the current config looks like a production deployment.

        Used by :meth:`check_production_readiness` to gate warnings that
        would be noise in local development.  Returns ``True`` when all
        three signals indicate non-dev use:

        * ``local_mode`` is off (private-IP SSRF bypass disabled)
        * ``no_auth`` is off (auth is actually required)
        * ``host`` is not bound to the loopback interface

        Returns:
            bool: True if the deployment looks production-like, False otherwise.
        """
        return (
            not self.server.local_mode
            and not self.auth.no_auth
            and self.server.host not in LOOPBACK_HOSTS
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

        if self.auth.no_auth and self.server.host not in LOOPBACK_HOSTS:
            if not self.server.unsafe_lan:
                warnings.append(
                    f"ERROR: no_auth is enabled while binding to {self.server.host!r} — an "
                    "unauthenticated admin UI on a network interface gives everyone on that "
                    "network full control. Bind to 127.0.0.1, enable authentication, or set "
                    "SHOREGUARD_UNSAFE_LAN=true (--unsafe-lan) if you accept the risk."
                )
            else:
                warnings.append(
                    f"WARN: unsafe_lan is set — serving an UNAUTHENTICATED admin UI on "
                    f"{self.server.host!r}; everyone who can reach this interface has full "
                    "control over all gateways and sandboxes"
                )

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

        # ── SSRF allowlist sanity ───────────────────────────────────────
        allowlist_entries = [
            p.strip() for p in self.server.ssrf_allowed_ips.split(",") if p.strip()
        ]
        for entry in allowlist_entries:
            # Entries are guaranteed parseable by the field validator.
            if ipaddress.ip_network(entry, strict=False).prefixlen == 0:
                warnings.append(
                    f"WARN: ssrf_allowed_ips contains {entry!r} — a /0 entry exempts every "
                    "address from the private/loopback SSRF rejection; allowlist only the "
                    "specific hosts or subnets you need"
                )
        if allowlist_entries and self.server.local_mode:
            warnings.append(
                "WARN: ssrf_allowed_ips is set while local_mode is on — local mode already "
                "bypasses the private-IP rejection, and an allowlisted private gateway is "
                "no longer eligible for the local-mode plaintext (no-mTLS) connection"
            )

        # ── CSP unsafe-* (only relevant when strict mode is off) ────────
        # When csp_strict=True the legacy csp_policy field is not used; the
        # header is built from csp_policy_strict, which has no 'unsafe-*'.
        if not self.auth.csp_strict and "'unsafe-" in self.auth.csp_policy:
            warnings.append(
                "ERROR: auth.csp_policy contains 'unsafe-*' directives "
                "(unsafe-inline / unsafe-eval) — XSS protection is degraded. "
                "Enable SHOREGUARD_CSP_STRICT=true (default) to use the "
                "nonce-based strict policy instead."
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
                if self.auth.secret_key is None:
                    warnings.append(
                        f"ERROR: SHOREGUARD_REPLICAS={replica_count} but auth.secret_key is "
                        "unset — each replica would derive its own on-disk key and sessions "
                        "would break on every load-balancer decision. Set SHOREGUARD_SECRET_KEY "
                        "to a stable 32+ char random string."
                    )
                warnings.append(
                    f"WARN: SHOREGUARD_REPLICAS={replica_count} but the rate limiters are "
                    "in-process only — limits do not coordinate across replicas "
                    "(Redis-backed limiter is a v1.x item)"
                )

            from shoreguard.config import default_database_url

            db_url = self.server.database_url or default_database_url()
            if db_url.startswith("sqlite"):
                # SQLite (WAL mode) is a supported single-replica deployment —
                # the homelab/single-box case. It only becomes a correctness
                # problem when multiple replicas write the same file.
                if replica_count > 1:
                    warnings.append(
                        f"ERROR: database_url is SQLite with SHOREGUARD_REPLICAS="
                        f"{replica_count} — concurrent replicas corrupt a shared "
                        "SQLite file; use PostgreSQL for multi-replica deployments"
                    )
                else:
                    warnings.append(
                        "WARN: database_url is SQLite in a production-like deployment — "
                        "fine for a single-replica box; use PostgreSQL for multi-replica "
                        "or write-heavy deployments"
                    )

            if self.server.log_format != "json":
                warnings.append(
                    f"WARN: server.log_format={self.server.log_format!r} in a production-like "
                    "deployment — set SHOREGUARD_LOG_FORMAT=json for machine-parseable logs"
                )

        for msg in warnings:
            logger.warning("Config check: %s", msg)
        return warnings

    def enforce_production_safety(self) -> None:
        """Refuse to start if any ``ERROR:``-severity config check fires.

        Calls :meth:`check_production_readiness` and raises ``RuntimeError``
        when any returned message is prefixed with ``ERROR:``. An operator
        can override the check with ``SHOREGUARD_ALLOW_UNSAFE_CONFIG=true``
        — useful for bringing up a broken stack for debugging — in which
        case the errors are logged at ``CRITICAL`` severity and startup
        continues.

        Raises:
            RuntimeError: If ``ERROR:`` messages are present and the
                override environment variable is not set to ``"true"``.
        """
        warnings = self.check_production_readiness()
        errors = [w for w in warnings if w.startswith("ERROR:")]
        if not errors:
            return
        if os.environ.get("SHOREGUARD_ALLOW_UNSAFE_CONFIG", "").lower() == "true":
            logger.critical(
                "SHOREGUARD_ALLOW_UNSAFE_CONFIG=true — starting with %d "
                "ERROR-severity config issue(s): %s",
                len(errors),
                "; ".join(errors),
            )
            return
        raise RuntimeError(
            f"Refusing to start with {len(errors)} prod-readiness ERROR(s): "
            f"{'; '.join(errors)}. Fix the config or set "
            "SHOREGUARD_ALLOW_UNSAFE_CONFIG=true to override."
        )

        return warnings


# ─── Singleton ────────────────────────────────────────────────────────────────

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the application settings singleton.

    On first call, reads all ``SHOREGUARD_*`` environment variables.
    Subsequent calls return the cached instance.

    Returns:
        Settings: The cached singleton Settings instance.
    """
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(settings: Settings) -> None:
    """Replace the settings singleton (for CLI overrides and tests).

    Args:
        settings: The replacement Settings instance to cache as the singleton.
    """
    global _settings  # noqa: PLW0603
    _settings = settings


def reset_settings() -> None:
    """Clear the cached singleton so the next ``get_settings()`` re-reads env."""
    global _settings  # noqa: PLW0603
    _settings = None
