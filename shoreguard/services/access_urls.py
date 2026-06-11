"""Reachable UI URLs for the "Open on phone" dialog.

The web UI is usually browsed via ``localhost`` — a QR code of that
URL is useless on any other device. This module answers "which
addresses would actually work from a phone": it inspects the
configured bind address and enumerates the host's non-loopback IPv4
addresses (``ip -j addr`` with a routing-socket fallback), so the UI
can either render a working QR code or say plainly that the server
only listens on loopback.

ShoreGuard serves plain HTTP when bound directly — TLS termination
lives in a reverse proxy, and an operator browsing through one is not
on a loopback hostname in the first place — so candidate URLs use the
``http`` scheme.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import shutil
import socket
import subprocess  # nosec B404
from typing import Any

from shoreguard.settings import LOOPBACK_HOSTS, get_settings

logger = logging.getLogger(__name__)

_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})  # nosec B104 # classifying, not binding


def _ip_command_addresses() -> list[str]:
    """Enumerate global-scope IPv4 addresses via ``ip -j addr``.

    Returns:
        list[str]: Addresses in interface order; empty when iproute2 is
        missing or its output cannot be parsed.
    """
    ip_bin = shutil.which("ip")
    if not ip_bin:
        return []
    try:
        proc = subprocess.run(  # nosec B603
            [ip_bin, "-j", "addr"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        interfaces = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        logger.debug("ip -j addr failed: %s", exc)
        return []
    addresses: list[str] = []
    for iface in interfaces:
        for info in iface.get("addr_info", []):
            if info.get("family") == "inet" and info.get("scope") == "global":
                addresses.append(info["local"])
    return addresses


def _default_route_address() -> str | None:
    """Find the IPv4 source address of the default route.

    Connecting a UDP socket sends no packets — it only asks the kernel
    which source address a packet to the target would use.

    Returns:
        str | None: The address, or None when the host has no route.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("203.0.113.1", 9))  # TEST-NET-3, never routed to
            return sock.getsockname()[0]
    except OSError:
        return None


def host_addresses() -> list[str]:
    """Enumerate non-loopback IPv4 addresses of this host, LAN-first.

    RFC1918 addresses sort before everything else (CGNAT/tailnet,
    public) because "phone on the same Wi-Fi" is the primary use case.

    Returns:
        list[str]: Deduplicated addresses; empty when none were found.
    """
    candidates = _ip_command_addresses()
    if not candidates:
        fallback = _default_route_address()
        candidates = [fallback] if fallback else []
    seen: set[str] = set()
    addresses: list[str] = []
    for raw in candidates:
        try:
            parsed = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local or raw in seen:
            continue
        seen.add(raw)
        addresses.append(raw)
    return sorted(addresses, key=lambda a: not ipaddress.ip_address(a).is_private)


def _format_host(host: str) -> str:
    """Wrap IPv6 literals in brackets for use inside a URL.

    Args:
        host: A hostname, IPv4, or IPv6 literal.

    Returns:
        str: The host as it may appear in a URL authority.
    """
    return f"[{host}]" if ":" in host else host


def access_urls() -> dict[str, Any]:
    """Describe how the running server can be reached from other devices.

    Reads the actual bind address/port from settings (the CLI pushes
    its resolved values there before starting uvicorn). ``lan_urls``
    lists candidate URLs a phone could open: for wildcard binds the
    host's enumerated addresses, for a specific non-loopback bind that
    address itself. For loopback binds ``lan_urls`` still carries the
    would-be addresses so the UI can name them in its hint, while
    ``loopback_only`` says they do not work right now.

    Returns:
        dict[str, Any]: ``{"bind_host", "port", "loopback_only", "lan_urls"}``.
    """
    server = get_settings().server
    host, port = server.host, server.port
    loopback_only = host in LOOPBACK_HOSTS
    if host in _WILDCARD_HOSTS or loopback_only:
        hosts = host_addresses()
    else:
        hosts = [host]
    return {
        "bind_host": host,
        "port": port,
        "loopback_only": loopback_only,
        "lan_urls": [f"http://{_format_host(h)}:{port}/" for h in hosts],
    }
