"""mTLS validation helpers for gateway channels.

Parses client and CA certificate bytes eagerly at channel-construction time
so that a bad bundle (expired, wrong SAN, malformed) fails immediately with a
structured :class:`GatewayNotConnectedError` rather than surviving until the
first RPC. Also emits a warning via the module logger when a certificate is
within the configured expiry warning window.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from shoreguard.exceptions import GatewayNotConnectedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CertInfo:
    """Parsed metadata about a validated gateway certificate.

    Attributes:
        not_after: UTC expiry timestamp of the client certificate.
        san_dns_names: DNS SANs present on the client certificate.
        seconds_until_expiry: Seconds from ``now`` until ``not_after`` at
            validation time. Non-positive means already expired.
    """

    not_after: _dt.datetime
    san_dns_names: tuple[str, ...]
    seconds_until_expiry: float


def _parse_cert(data: bytes, *, label: str) -> x509.Certificate:
    """Parse a PEM or DER encoded X.509 certificate.

    Args:
        data: Raw certificate bytes.
        label: Human-friendly label used in error messages.

    Returns:
        x509.Certificate: The parsed certificate.

    Raises:
        GatewayNotConnectedError: If the bytes cannot be parsed as a
            certificate in either encoding.
    """
    errors: list[str] = []
    for loader in (x509.load_pem_x509_certificate, x509.load_der_x509_certificate):
        try:
            return loader(data)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{loader.__name__}: {exc}")
    raise GatewayNotConnectedError(f"Failed to parse {label}: {'; '.join(errors)}")


def _parse_key(data: bytes) -> None:
    """Validate that ``data`` is a parseable private key.

    Args:
        data: Raw private-key bytes (PEM).

    Raises:
        GatewayNotConnectedError: If the bytes cannot be parsed as a private
            key.
    """
    try:
        serialization.load_pem_private_key(data, password=None)
    except Exception as exc:  # noqa: BLE001
        raise GatewayNotConnectedError(f"Failed to parse client private key: {exc}") from exc


def _extract_san_dns(cert: x509.Certificate) -> tuple[str, ...]:
    """Return the DNS SANs on a certificate, or an empty tuple.

    Args:
        cert: Parsed certificate.

    Returns:
        tuple[str, ...]: Lower-cased DNS SAN entries.
    """
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return ()
    return tuple(sorted({name.lower() for name in ext.value.get_values_for_type(x509.DNSName)}))


def validate_bundle(
    *,
    ca_cert: bytes,
    client_cert: bytes,
    client_key: bytes,
    endpoint_host: str,
    warn_within_days: int = 14,
    now: _dt.datetime | None = None,
) -> CertInfo:
    """Eagerly validate an mTLS bundle and return metadata about the client cert.

    The check is intentionally lenient: it rejects things that will certainly
    break the channel (unparseable bytes, expired cert, endpoint host not in
    the SAN list) but does not try to re-validate the full chain — gRPC/BoringSSL
    already does that during the handshake.

    Args:
        ca_cert: CA certificate bytes (PEM or DER).
        client_cert: Client certificate bytes (PEM or DER).
        client_key: Client private key bytes (PEM).
        endpoint_host: Hostname portion of the gateway endpoint. IP literals
            skip the SAN check because gRPC overrides SNI via the channel
            options rather than the cert SAN.
        warn_within_days: Emit a ``logger.warning`` when ``not_after`` is
            within this many days.
        now: Override the current time for tests.

    Returns:
        CertInfo: Parsed client-cert metadata.

    Raises:
        GatewayNotConnectedError: If any of the bundle pieces fail validation.
    """
    now = now or _dt.datetime.now(_dt.UTC)
    _parse_cert(ca_cert, label="CA certificate")
    cert = _parse_cert(client_cert, label="client certificate")
    _parse_key(client_key)

    not_after = cert.not_valid_after_utc
    seconds_until_expiry = (not_after - now).total_seconds()
    if seconds_until_expiry <= 0:
        raise GatewayNotConnectedError(f"Client certificate expired at {not_after.isoformat()}")

    san_dns = _extract_san_dns(cert)
    if _is_hostname(endpoint_host) and san_dns and not _san_matches(san_dns, endpoint_host):
        raise GatewayNotConnectedError(
            f"Endpoint host {endpoint_host!r} is not covered by client cert SANs {list(san_dns)}"
        )

    if seconds_until_expiry <= warn_within_days * 86400:
        logger.warning(
            "Gateway client certificate expires soon: not_after=%s, host=%s",
            not_after.isoformat(),
            endpoint_host,
        )

    return CertInfo(
        not_after=not_after,
        san_dns_names=san_dns,
        seconds_until_expiry=seconds_until_expiry,
    )


def _is_hostname(value: str) -> bool:
    """Return True if ``value`` looks like a DNS hostname (not an IP literal).

    Args:
        value: Candidate host string.

    Returns:
        bool: ``True`` when the value should be matched against DNS SANs.
    """
    if not value:
        return False
    if value.replace(".", "").isdigit():
        return False
    if ":" in value:  # IPv6
        return False
    return True


def _san_matches(san_dns: tuple[str, ...], host: str) -> bool:
    """Match a hostname against a DNS SAN list, honoring leading wildcards.

    Args:
        san_dns: DNS SANs extracted from the certificate, lower-cased.
        host: Hostname to match.

    Returns:
        bool: ``True`` when the host is covered by any SAN entry.
    """
    host = host.lower()
    for san in san_dns:
        if san == host:
            return True
        if san.startswith("*.") and host.endswith(san[1:]) and "." in host:
            # Wildcard matches exactly one label.
            host_prefix = host[: -len(san) + 1]
            if "." not in host_prefix:
                return True
    return False
