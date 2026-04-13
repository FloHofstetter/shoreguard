"""Unit tests for mTLS hardening: validation, require_mtls, reload_credentials."""

from __future__ import annotations

import datetime as _dt
from unittest.mock import MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from shoreguard.client import ShoreGuardClient
from shoreguard.client._tls import validate_bundle
from shoreguard.exceptions import GatewayNotConnectedError


def _make_cert(
    *,
    common_name: str = "shoreguard-test",
    dns_names: list[str] | None = None,
    not_after_days: int = 30,
) -> tuple[bytes, bytes]:
    """Generate a self-signed certificate + private key in PEM form.

    Returns:
        tuple[bytes, bytes]: ``(cert_pem, key_pem)``.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = _dt.datetime.now(_dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=not_after_days))
    )
    if dns_names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in dns_names]),
            critical=False,
        )
    cert = builder.sign(private_key=key, algorithm=hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


@pytest.fixture
def valid_bundle():
    cert, key = _make_cert(dns_names=["gateway.local"], not_after_days=365)
    return {"ca_cert": cert, "client_cert": cert, "client_key": key}


# ── validate_bundle ─────────────────────────────────────────────────────────


def test_validate_bundle_accepts_matching_san(valid_bundle):
    info = validate_bundle(endpoint_host="gateway.local", **valid_bundle)
    assert "gateway.local" in info.san_dns_names
    assert info.seconds_until_expiry > 0


def test_validate_bundle_rejects_expired_cert():
    cert, key = _make_cert(dns_names=["gateway.local"], not_after_days=1)
    # Simulate being two days past the expiry.
    future = _dt.datetime.now(_dt.UTC) + _dt.timedelta(days=3)
    with pytest.raises(GatewayNotConnectedError, match="expired"):
        validate_bundle(
            ca_cert=cert,
            client_cert=cert,
            client_key=key,
            endpoint_host="gateway.local",
            now=future,
        )


def test_validate_bundle_rejects_san_mismatch():
    cert, key = _make_cert(dns_names=["other.local"], not_after_days=365)
    with pytest.raises(GatewayNotConnectedError, match="SAN"):
        validate_bundle(
            ca_cert=cert,
            client_cert=cert,
            client_key=key,
            endpoint_host="gateway.local",
        )


def test_validate_bundle_skips_san_check_for_ip_literal():
    cert, key = _make_cert(dns_names=["gateway.local"], not_after_days=365)
    info = validate_bundle(
        ca_cert=cert,
        client_cert=cert,
        client_key=key,
        endpoint_host="127.0.0.1",
    )
    assert info.seconds_until_expiry > 0


def test_validate_bundle_warns_when_near_expiry(caplog):
    cert, key = _make_cert(dns_names=["gateway.local"], not_after_days=3)
    with caplog.at_level("WARNING"):
        validate_bundle(
            ca_cert=cert,
            client_cert=cert,
            client_key=key,
            endpoint_host="gateway.local",
            warn_within_days=14,
        )
    assert any("expires soon" in r.message for r in caplog.records)


def test_validate_bundle_rejects_unparseable_bytes():
    with pytest.raises(GatewayNotConnectedError, match="Failed to parse"):
        validate_bundle(
            ca_cert=b"not-a-cert",
            client_cert=b"not-a-cert",
            client_key=b"not-a-key",
            endpoint_host="gateway.local",
        )


# ── require_mtls enforcement ────────────────────────────────────────────────


def test_require_mtls_rejects_plaintext_constructor():
    with pytest.raises(GatewayNotConnectedError, match="mTLS required"):
        ShoreGuardClient("localhost:8080", require_mtls=True)


def test_require_mtls_default_constructor_allows_plaintext(monkeypatch):
    monkeypatch.setattr("grpc.insecure_channel", lambda ep: MagicMock())
    c = ShoreGuardClient("localhost:8080")
    assert c._channel is not None


def test_from_credentials_rejects_plaintext_when_required(monkeypatch):
    monkeypatch.setattr("shoreguard.client._default_require_mtls", lambda: True)
    with pytest.raises(GatewayNotConnectedError, match="mTLS required"):
        ShoreGuardClient.from_credentials("gateway.local:443")


def test_from_credentials_accepts_valid_bundle(valid_bundle, monkeypatch):
    monkeypatch.setattr("shoreguard.client._default_require_mtls", lambda: True)
    monkeypatch.setattr("grpc.ssl_channel_credentials", lambda **kw: MagicMock())
    monkeypatch.setattr("grpc.secure_channel", lambda ep, creds: MagicMock())
    c = ShoreGuardClient.from_credentials("gateway.local:443", **valid_bundle)
    assert c.cert_info is not None
    assert "gateway.local" in c.cert_info.san_dns_names


# ── reload_credentials ──────────────────────────────────────────────────────


def test_reload_credentials_rebuilds_channel_and_stubs(valid_bundle, monkeypatch):
    monkeypatch.setattr("shoreguard.client._default_require_mtls", lambda: True)
    monkeypatch.setattr("grpc.ssl_channel_credentials", lambda **kw: MagicMock())
    monkeypatch.setattr("grpc.secure_channel", lambda ep, creds: MagicMock())

    c = ShoreGuardClient.from_credentials("gateway.local:443", **valid_bundle)
    original_channel = c._channel
    original_sandboxes = c.sandboxes

    new_cert, new_key = _make_cert(dns_names=["gateway.local"], not_after_days=180)
    c.reload_credentials(ca_cert=new_cert, client_cert=new_cert, client_key=new_key)

    assert c._channel is not original_channel
    assert c.sandboxes is not original_sandboxes  # stubs rebuilt → new manager
    original_channel.close.assert_called_once()  # type: ignore[attr-defined]


def test_reload_credentials_rejects_bad_bundle(valid_bundle, monkeypatch):
    monkeypatch.setattr("shoreguard.client._default_require_mtls", lambda: True)
    monkeypatch.setattr("grpc.ssl_channel_credentials", lambda **kw: MagicMock())
    monkeypatch.setattr("grpc.secure_channel", lambda ep, creds: MagicMock())

    c = ShoreGuardClient.from_credentials("gateway.local:443", **valid_bundle)
    with pytest.raises(GatewayNotConnectedError):
        c.reload_credentials(ca_cert=b"garbage", client_cert=b"garbage", client_key=b"garbage")
