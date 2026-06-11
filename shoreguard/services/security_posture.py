"""Security posture self-check for the running deployment.

Answers the homelab operator's question "am I exposed?" as structured
data: bind address vs. auth mode, unsafe-LAN opt-ins, secret-key
hygiene, open registration, HSTS/CSP, per-gateway transport security
(mTLS vs. the local-mode plaintext exemption), and whether Tailscale is
available as the recommended remote-access path. The REST layer
(``GET /api/security/posture``) returns this verbatim; the Security
page renders it with one-click fix hints.

The check NEVER mutates anything and avoids slow probes — it reads
settings, the gateway registry, and the local filesystem/interface
list, so it is cheap enough to render on every page load.
"""

from __future__ import annotations

import socket
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from shoreguard.settings import LOOPBACK_HOSTS

if TYPE_CHECKING:
    from shoreguard.services.registry import GatewayRegistry
    from shoreguard.settings import Settings

Severity = Literal["ok", "info", "warn", "error"]

_TAILSCALE_SOCKETS = (
    Path("/var/run/tailscale/tailscaled.sock"),
    Path("/run/tailscale/tailscaled.sock"),
)


@dataclass(frozen=True)
class PostureCheck:
    """One security posture finding.

    Attributes:
        id: Stable machine-readable check identifier.
        severity: One of ``ok``, ``info``, ``warn``, ``error``.
        title: Short human-readable check name.
        detail: What was found, in one or two sentences.
        fix: Actionable remediation hint, or ``None`` when nothing to do.
    """

    id: str
    severity: Severity
    title: str
    detail: str
    fix: str | None = None


def _is_private_endpoint(endpoint: str) -> bool:
    """Return True when a gateway endpoint host is loopback or RFC-1918 private.

    Args:
        endpoint: ``host:port`` endpoint string from the registry.

    Returns:
        bool: True for loopback/private hosts (best effort; unresolvable
            hostnames count as not private).
    """
    import ipaddress

    host = endpoint.rsplit(":", 1)[0].strip("[]")
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def tailscale_detected() -> bool:
    """Best-effort detection of a running Tailscale daemon on this host.

    Returns:
        bool: True if a tailscaled socket or a ``tailscale*`` network
            interface is present.
    """
    if any(p.exists() for p in _TAILSCALE_SOCKETS):
        return True
    try:
        return any(name.startswith("tailscale") for _, name in socket.if_nameindex())
    except OSError:  # pragma: no cover — platform without if_nameindex
        return False


def _auth_checks(settings: Settings) -> list[PostureCheck]:
    """Build the auth/bind related posture checks.

    Args:
        settings: The active settings snapshot.

    Returns:
        list[PostureCheck]: Auth, bind, secret-key, and registration checks.
    """
    checks: list[PostureCheck] = []
    host = settings.server.host
    loopback = host in LOOPBACK_HOSTS

    if settings.auth.no_auth:
        if loopback:
            checks.append(
                PostureCheck(
                    id="auth_mode",
                    severity="warn",
                    title="Authentication disabled",
                    detail="--no-auth is active. Bound to loopback, so only local "
                    "processes can reach the UI — acceptable for development.",
                    fix="For anything reachable beyond this machine, switch to "
                    "--single-user or full user management.",
                )
            )
        else:
            checks.append(
                PostureCheck(
                    id="auth_mode",
                    severity="error",
                    title="Unauthenticated UI on a network interface",
                    detail=f"--no-auth is active while binding to {host!r}. Everyone "
                    "who can reach this interface has full admin control — this is "
                    "the misconfiguration behind the 2026 exposed-agent-gateway wave.",
                    fix="Bind to 127.0.0.1, or enable authentication "
                    "(--single-user is the one-flag option).",
                )
            )
    else:
        mode = "single-user" if settings.auth.single_user else "multi-user RBAC"
        checks.append(
            PostureCheck(
                id="auth_mode",
                severity="ok",
                title="Authentication enabled",
                detail=f"Sessions and roles are enforced ({mode}).",
            )
        )

    if settings.server.unsafe_lan:
        checks.append(
            PostureCheck(
                id="unsafe_lan",
                severity="error",
                title="unsafe-lan override active",
                detail="SHOREGUARD_UNSAFE_LAN explicitly allows the unauthenticated "
                "UI on a non-loopback interface.",
                fix="Remove --unsafe-lan and use --single-user (or Tailscale "
                "Serve in front of a loopback bind) instead.",
            )
        )

    checks.append(
        PostureCheck(
            id="bind_address",
            severity="ok" if loopback else "info",
            title="Bind address",
            detail=f"Serving on {host}."
            + ("" if loopback else " Make sure a TLS proxy or VPN sits in front."),
            fix=None
            if loopback
            else "Prefer binding to 127.0.0.1 and exposing via `tailscale serve` "
            "or a reverse proxy that terminates TLS.",
        )
    )

    if not settings.auth.no_auth:
        if settings.auth.secret_key is None:
            checks.append(
                PostureCheck(
                    id="secret_key",
                    severity="info",
                    title="Secret key from disk",
                    detail="SHOREGUARD_SECRET_KEY is unset; using the generated "
                    "on-disk .secret_key (fine for a single box).",
                    fix="Set SHOREGUARD_SECRET_KEY for container or multi-replica "
                    "deployments so sessions survive re-provisioning.",
                )
            )
        elif len(settings.auth.secret_key) < 32:
            checks.append(
                PostureCheck(
                    id="secret_key",
                    severity="error",
                    title="Secret key too short",
                    detail=f"SHOREGUARD_SECRET_KEY is only "
                    f"{len(settings.auth.secret_key)} characters.",
                    fix="Use at least 32 random characters (`openssl rand -hex 32`).",
                )
            )
        else:
            checks.append(
                PostureCheck(
                    id="secret_key",
                    severity="ok",
                    title="Secret key configured",
                    detail="A sufficiently long explicit secret key is set.",
                )
            )

    if settings.auth.allow_registration:
        checks.append(
            PostureCheck(
                id="registration",
                severity="error" if not loopback else "warn",
                title="Self-registration open",
                detail="SHOREGUARD_ALLOW_REGISTRATION lets anyone who can reach "
                "the UI create an account.",
                fix="Disable it and invite users explicitly from the Users page.",
            )
        )

    if not loopback and not settings.auth.no_auth and not settings.auth.hsts_enabled:
        checks.append(
            PostureCheck(
                id="hsts",
                severity="warn",
                title="HSTS disabled",
                detail="Serving on a network interface without Strict-Transport-Security.",
                fix="Set SHOREGUARD_HSTS_ENABLED=true when an HTTPS proxy fronts ShoreGuard.",
            )
        )

    if not settings.auth.csp_strict:
        checks.append(
            PostureCheck(
                id="csp",
                severity="warn",
                title="Strict CSP disabled",
                detail="The legacy Content-Security-Policy is in use instead of "
                "the nonce-based strict policy.",
                fix="Remove SHOREGUARD_CSP_STRICT=false — the strict default "
                "works with the bundled frontend.",
            )
        )

    return checks


def _gateway_checks(gateways: list[dict[str, Any]], settings: Settings) -> list[PostureCheck]:
    """Build per-gateway transport security checks.

    Args:
        gateways: Registry rows (``has_ca_cert`` / ``has_client_cert`` flags).
        settings: The active settings snapshot.

    Returns:
        list[PostureCheck]: One check per registered gateway.
    """
    checks: list[PostureCheck] = []
    for gw in gateways:
        name = gw.get("name", "?")
        endpoint = str(gw.get("endpoint", ""))
        has_bundle = bool(gw.get("has_ca_cert")) and bool(gw.get("has_client_cert"))
        private = _is_private_endpoint(endpoint)
        if has_bundle:
            checks.append(
                PostureCheck(
                    id=f"gateway:{name}",
                    severity="ok",
                    title=f"Gateway “{name}”: mTLS",
                    detail=f"Mutual TLS credentials are configured for {endpoint}.",
                )
            )
        elif private and settings.server.local_mode:
            checks.append(
                PostureCheck(
                    id=f"gateway:{name}",
                    severity="info",
                    title=f"Gateway “{name}”: local plaintext",
                    detail=f"No mTLS bundle; {endpoint} is loopback/private and "
                    "local mode permits plaintext gRPC.",
                    fix="Add an mTLS bundle before this gateway leaves the box.",
                )
            )
        else:
            checks.append(
                PostureCheck(
                    id=f"gateway:{name}",
                    severity="error" if not private else "warn",
                    title=f"Gateway “{name}”: no mTLS",
                    detail=f"No mTLS credentials are stored for {endpoint}"
                    + ("" if private else " — a non-private endpoint"),
                    fix="Upload the gateway's CA/client certificate bundle on "
                    "the gateway settings page.",
                )
            )
    return checks


async def collect_posture(settings: Settings, registry: GatewayRegistry) -> dict[str, Any]:
    """Collect the full security posture report.

    Args:
        settings: The active settings snapshot.
        registry: Gateway registry for per-gateway transport checks.

    Returns:
        dict[str, Any]: ``{"checks": [...], "summary": {...},
            "tailscale": bool}`` ready for JSON serialization.
    """
    checks = _auth_checks(settings)
    try:
        gateways = await registry.list_all()
    except Exception:  # noqa: BLE001 — posture must render even if the DB hiccups
        gateways = []
        checks.append(
            PostureCheck(
                id="registry",
                severity="warn",
                title="Gateway registry unavailable",
                detail="Could not read registered gateways for transport checks.",
            )
        )
    checks.extend(_gateway_checks(gateways, settings))

    ts = tailscale_detected()
    checks.append(
        PostureCheck(
            id="tailscale",
            severity="ok" if ts else "info",
            title="Tailscale" if ts else "Tailscale not detected",
            detail="A Tailscale daemon is running on this host — `tailscale serve` "
            "is the recommended way to reach this UI remotely."
            if ts
            else "No Tailscale daemon found. For remote/phone access, a mesh VPN "
            "beats exposing ports.",
            fix=None if ts else "See the homelab guide for the Tailscale recipe.",
        )
    )

    summary = {sev: 0 for sev in ("ok", "info", "warn", "error")}
    for c in checks:
        summary[c.severity] += 1
    return {
        "checks": [asdict(c) for c in checks],
        "summary": summary,
        "tailscale": ts,
    }
