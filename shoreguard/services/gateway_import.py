"""Import OpenShell filesystem gateways into the DB registry.

Shared between the ``shoreguard import-gateways`` CLI command and the
local-mode auto-import that runs in the app lifespan.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shoreguard.services.registry import GatewayRegistry

logger = logging.getLogger(__name__)

def import_filesystem_gateways(
    registry: GatewayRegistry,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Import gateways from openshell filesystem config into the DB registry.

    Gateways already in the DB are silently skipped.

    Args:
        registry: Gateway registry to import into.
        log_fn: Callback for status lines; falls back to module logger.

    Returns:
        tuple[int, int]: ``(imported, skipped)`` counts.
    """
    import json as json_mod
    from urllib.parse import urlparse

    from shoreguard.config import (
        ENDPOINT_RE as _ENDPOINT_RE,
    )
    from shoreguard.config import (
        VALID_GATEWAY_NAME_RE as _VALID_IMPORT_NAME_RE,
    )
    from shoreguard.config import is_private_ip, openshell_config_dir
    from shoreguard.settings import get_settings

    def _log(msg: str, *, level: int = logging.INFO) -> None:
        if log_fn is not None:
            log_fn(msg)
        else:
            logger.log(level, msg)

    gateways_dir = openshell_config_dir() / "gateways"
    if not gateways_dir.exists():
        _log(f"No filesystem gateways found at {gateways_dir}")
        return 0, 0

    imported = 0
    skipped = 0
    for entry in sorted(gateways_dir.iterdir()):
        if not entry.is_dir():
            continue
        metadata_file = entry / "metadata.json"
        if not metadata_file.exists():
            continue

        name = entry.name
        if not _VALID_IMPORT_NAME_RE.match(name):
            _log(f"  skip  {name} (invalid name format)")
            skipped += 1
            continue
        if registry.get(name) is not None:
            _log(f"  skip  {name} (already registered)")
            skipped += 1
            continue

        try:
            metadata = json_mod.loads(metadata_file.read_text())
        except (json_mod.JSONDecodeError, OSError) as e:
            _log(f"  error {name}: {e}", level=logging.WARNING)
            skipped += 1
            continue

        endpoint = metadata.get("gateway_endpoint", "")
        scheme = "https" if "https" in endpoint else "http"
        auth_mode = metadata.get("auth_mode")

        ca_cert = None
        client_cert = None
        client_key = None
        _max_cert = get_settings().limits.max_cert_bytes
        mtls_dir = entry / "mtls"
        if scheme == "https" and mtls_dir.exists():
            ca_file = mtls_dir / "ca.crt"
            cert_file = mtls_dir / "tls.crt"
            key_file = mtls_dir / "tls.key"
            try:
                if ca_file.exists():
                    ca_cert = ca_file.read_bytes()
                if cert_file.exists():
                    client_cert = cert_file.read_bytes()
                if key_file.exists():
                    client_key = key_file.read_bytes()
            except OSError as e:
                _log(f"  error {name}: failed to read mTLS certs: {e}", level=logging.WARNING)
                skipped += 1
                continue
            cert_fields = [
                ("ca_cert", ca_cert),
                ("client_cert", client_cert),
                ("client_key", client_key),
            ]
            for label, blob in cert_fields:
                if blob is not None and len(blob) > _max_cert:
                    _log(
                        f"  skip  {name} ({label} exceeds {_max_cert} bytes)",
                        level=logging.WARNING,
                    )
                    skipped += 1
                    break
            else:
                # Only reached when no cert exceeded the limit (no break).
                pass
            if any(
                blob is not None and len(blob) > _max_cert
                for blob in (ca_cert, client_cert, client_key)
            ):
                continue

        meta = {
            "gpu": metadata.get("gpu", False),
            "is_remote": metadata.get("is_remote", False),
            "remote_host": metadata.get("remote_host"),
        }

        parsed = urlparse(endpoint)
        host = parsed.hostname
        if not host:
            _log(f"  skip  {name} (no hostname in endpoint '{endpoint}')")
            skipped += 1
            continue
        port = parsed.port or (443 if scheme == "https" else 80)
        clean_endpoint = f"{host}:{port}"

        if is_private_ip(host) and not get_settings().server.local_mode:
            _log(
                f"  skip  {name} (private/loopback address: '{host}' — exempt via "
                "SHOREGUARD_SSRF_ALLOWED_IPS or run with --local)",
                level=logging.WARNING,
            )
            skipped += 1
            continue
        if not _ENDPOINT_RE.match(clean_endpoint):
            _log(f"  skip  {name} (invalid endpoint format: '{clean_endpoint}')")
            skipped += 1
            continue
        ep_port = int(clean_endpoint.rsplit(":", 1)[1])
        if ep_port < 1 or ep_port > 65535:
            _log(f"  skip  {name} (port out of range: {ep_port})")
            skipped += 1
            continue

        try:
            registry.register(
                name,
                clean_endpoint,
                scheme,
                auth_mode,
                ca_cert=ca_cert,
                client_cert=client_cert,
                client_key=client_key,
                metadata=meta,
            )
        except ValueError as e:
            _log(f"  error  {name}: {e}", level=logging.WARNING)
            skipped += 1
            continue
        except Exception as e:
            _log(f"  error  {name}: unexpected error: {e}", level=logging.ERROR)
            skipped += 1
            continue
        _log(f"  imported {name} ({clean_endpoint})")
        imported += 1

    return imported, skipped

