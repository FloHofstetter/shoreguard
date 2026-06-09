"""Shared configuration helpers for Shoreguard."""

from __future__ import annotations

import functools
import ipaddress
import os
import re
import socket
from pathlib import Path

# ─── Shared validation constants ────���────────────────────────────────────────

VALID_GATEWAY_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,252}$")
ENDPOINT_RE = re.compile(r"^[a-zA-Z0-9._-]+:\d{1,5}$")


def _parse_cidr_list(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse a comma-separated CIDR list, skipping unparsable entries.

    Entries are validated at settings load time, so parsing failures here
    are unreachable in practice.

    Args:
        raw: Comma-separated IPs or CIDR ranges.

    Returns:
        tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
            Parsed CIDR networks.
    """
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in (p.strip() for p in raw.split(",") if p.strip()):
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(nets)


@functools.lru_cache(maxsize=1)
def _always_blocked_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse the operator-configured always-blocked CIDR list.

    Reads ``SHOREGUARD_ALWAYS_BLOCKED_IPS`` via :class:`ServerSettings`.
    Mirrors upstream OpenShell #814: gives operators one chokepoint to
    hard-block egress targets (metadata VIPs, known-bad nets) beyond the
    RFC-based private-address checks.

    Returns:
        tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
            Parsed CIDR networks, or ``()`` if the setting is empty or
            the settings singleton is not yet initialised.
    """
    try:
        from shoreguard.settings import get_settings

        raw = get_settings().server.always_blocked_ips
    except Exception:  # noqa: BLE001 — settings not initialised yet
        return ()
    return _parse_cidr_list(raw)


@functools.lru_cache(maxsize=1)
def _ssrf_allowed_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse the operator-configured SSRF allowlist CIDR list.

    Reads ``SHOREGUARD_SSRF_ALLOWED_IPS`` via :class:`ServerSettings`.
    When the settings singleton is not yet initialised this returns ``()``,
    which for an allowlist fails *closed* (nothing is exempted) — the safe
    direction.

    Returns:
        tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
            Parsed CIDR networks, or ``()`` if the setting is empty or
            the settings singleton is not yet initialised.
    """
    try:
        from shoreguard.settings import get_settings

        raw = get_settings().server.ssrf_allowed_ips
    except Exception:  # noqa: BLE001 — settings not initialised yet
        return ()
    return _parse_cidr_list(raw)


def _in_networks(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    nets: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> bool:
    """Return True if *addr* falls inside any of the given CIDR networks.

    Args:
        addr: Parsed IP address to test.
        nets: CIDR networks to test against.

    Returns:
        bool: ``True`` if *addr* matches one of the networks.
    """
    for net in nets:
        if addr.version != net.version:
            continue
        if addr in net:
            return True
    return False


def _in_always_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if *addr* falls inside any configured always-blocked CIDR.

    Args:
        addr: Parsed IP address to test.

    Returns:
        bool: ``True`` if *addr* matches one of the configured networks.
    """
    return _in_networks(addr, _always_blocked_networks())


def _in_ssrf_allowed(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if *addr* falls inside any configured SSRF-allowlist CIDR.

    Args:
        addr: Parsed IP address to test.

    Returns:
        bool: ``True`` if *addr* matches one of the configured networks.
    """
    return _in_networks(addr, _ssrf_allowed_networks())


def is_private_ip(host: str) -> bool:
    """Return True if *host* resolves to a private/loopback/link-local address.

    Used both at registration time (API validation) and at connection time
    (DNS-rebinding protection). Honours two operator-configured CIDR lists,
    both parsed once at startup:

    * ``SHOREGUARD_ALWAYS_BLOCKED_IPS`` hard-blocks additional ranges (cloud
      metadata VIPs, internal management subnets) and takes precedence over
      everything else.
    * ``SHOREGUARD_SSRF_ALLOWED_IPS`` exempts specific ranges from the RFC
      private/loopback/link-local/reserved checks (e.g. a homelab OIDC
      provider). Matching happens on the *resolved* address, so a hostname
      is exempt only if it resolves into an allowlisted range. The literal
      hostnames ``localhost``/``localhost.localdomain`` are always treated
      as private regardless of the allowlist — use an IP literal and
      allowlist ``127.0.0.1``/``::1`` to exempt loopback.

    Args:
        host: IP address literal or hostname to check.

    Returns:
        bool: ``True`` if the address is in the always-blocked list, or is
            private/loopback/link-local/reserved and not exempted by the
            SSRF allowlist.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        if host.lower() in ("localhost", "localhost.localdomain"):
            return True
        try:
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(2.0)
            try:
                resolved = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            finally:
                socket.setdefaulttimeout(old_timeout)
            if not resolved:
                return False
            addr = ipaddress.ip_address(resolved[0][4][0])
        except TimeoutError, socket.gaierror, ValueError, IndexError, OSError:
            return False
    if _in_always_blocked(addr):
        return True
    if _in_ssrf_allowed(addr):
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


def xdg_config_home() -> Path:
    """Return the XDG config home directory.

    Returns:
        Path: Path from ``$XDG_CONFIG_HOME`` or ``~/.config`` as fallback.
    """
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".config"


def openshell_config_dir() -> Path:
    """Return the openshell config directory.

    Returns:
        Path: ``<xdg_config_home>/openshell``.
    """
    return xdg_config_home() / "openshell"


def shoreguard_config_dir() -> Path:
    """Return the shoreguard config directory.

    Returns:
        Path: ``<xdg_config_home>/shoreguard``.
    """
    return xdg_config_home() / "shoreguard"


def default_database_url() -> str:
    """Return the database URL from Settings, env, or SQLite default.

    Checks the Settings singleton first (which reads ``SHOREGUARD_DATABASE_URL``
    via pydantic-settings).  Falls back to the env var directly for early
    startup before Settings is initialised.

    Returns:
        str: Resolved database URL.
    """
    try:
        from shoreguard.settings import get_settings

        url = get_settings().server.database_url
        if url:
            return url
    except Exception:  # noqa: BLE001 — startup fallback
        pass

    env_url = os.environ.get("SHOREGUARD_DATABASE_URL")
    if env_url:
        return env_url
    db_path = shoreguard_config_dir() / "shoreguard.db"
    return f"sqlite:///{db_path}"
