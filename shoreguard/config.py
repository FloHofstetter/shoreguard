"""Shared configuration helpers for Shoreguard."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from pathlib import Path

# ─── Shared validation constants ────���────────────────────────────────────────

VALID_GATEWAY_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,252}$")
ENDPOINT_RE = re.compile(r"^[a-zA-Z0-9._-]+:\d{1,5}$")


def is_private_ip(host: str) -> bool:
    """Return True if *host* resolves to a private/loopback/link-local address.

    Used both at registration time (API validation) and at connection time
    (DNS-rebinding protection).

    Args:
        host: IP address literal or hostname to check.

    Returns:
        bool: ``True`` if the address is private, loopback, link-local,
            or reserved.
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
    """Return the database URL, from env or SQLite default.

    Returns:
        str: Value of ``$SHOREGUARD_DATABASE_URL`` or a SQLite file URL.
    """
    env_url = os.environ.get("SHOREGUARD_DATABASE_URL")
    if env_url:
        return env_url
    db_path = shoreguard_config_dir() / "shoreguard.db"
    return f"sqlite:///{db_path}"
