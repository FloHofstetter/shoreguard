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


@functools.lru_cache(maxsize=1)
def _always_blocked_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse the operator-configured always-blocked CIDR list.

    Reads ``SHOREGUARD_ALWAYS_BLOCKED_IPS`` via :class:`ServerSettings`.
    Mirrors upstream OpenShell #814: gives operators one chokepoint to
    hard-block egress targets (metadata VIPs, known-bad nets) beyond the
    RFC-based private-address checks. Entries are validated at settings
    load time, so parsing failures here are unreachable.

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
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in (p.strip() for p in raw.split(",") if p.strip()):
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _in_always_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if *addr* falls inside any configured always-blocked CIDR.

    Args:
        addr: Parsed IP address to test.

    Returns:
        bool: ``True`` if *addr* matches one of the configured networks.
    """
    for net in _always_blocked_networks():
        if addr.version != net.version:
            continue
        if addr in net:
            return True
    return False


def is_private_ip(host: str) -> bool:
    """Return True if *host* resolves to a private/loopback/link-local address.

    Used both at registration time (API validation) and at connection time
    (DNS-rebinding protection). Also honours ``SHOREGUARD_ALWAYS_BLOCKED_IPS``
    so operators can hard-block additional ranges (cloud metadata VIPs,
    internal management subnets) without code changes.

    Args:
        host: IP address literal or hostname to check.

    Returns:
        bool: ``True`` if the address is private, loopback, link-local,
            reserved, or in the configured always-blocked list.
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
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        return True
    return _in_always_blocked(addr)


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
