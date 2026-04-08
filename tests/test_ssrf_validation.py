"""Unit tests for SSRF protection helpers and endpoint validation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from shoreguard.api.routes.gateway import _validate_endpoint_format
from shoreguard.api.validation import validate_smtp_host, validate_webhook_url
from shoreguard.config import is_private_ip as _is_private_ip
from shoreguard.exceptions import ValidationError as DomainValidationError

# ─── _is_private_ip ──────────────────────────────────────────────────────────


class TestIsPrivateIp:
    def test_loopback_ipv4(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_loopback_ipv4_non_standard(self):
        assert _is_private_ip("127.0.0.2") is True

    def test_loopback_ipv6(self):
        assert _is_private_ip("::1") is True

    def test_rfc1918_10(self):
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("10.255.255.255") is True

    def test_rfc1918_172(self):
        assert _is_private_ip("172.16.0.1") is True
        assert _is_private_ip("172.31.255.255") is True

    def test_rfc1918_192(self):
        assert _is_private_ip("192.168.0.1") is True
        assert _is_private_ip("192.168.255.255") is True

    def test_link_local(self):
        assert _is_private_ip("169.254.1.1") is True

    def test_reserved_zero(self):
        assert _is_private_ip("0.0.0.0") is True

    def test_public_ip(self):
        assert _is_private_ip("8.8.8.8") is False
        assert _is_private_ip("1.1.1.1") is False
        assert _is_private_ip("203.0.113.1") is True  # TEST-NET-3, reserved

    def test_localhost_hostname(self):
        assert _is_private_ip("localhost") is True

    def test_localhost_localdomain(self):
        assert _is_private_ip("localhost.localdomain") is True

    def test_hostname_resolving_to_private(self):
        """Hostname that resolves to a private IP should be blocked."""
        fake_result = [(2, 1, 6, "", ("10.0.0.5", 0))]
        with patch("shoreguard.config.socket.getaddrinfo", return_value=fake_result):
            assert _is_private_ip("internal.example.com") is True

    def test_hostname_resolving_to_public(self):
        """Hostname that resolves to a public IP should be allowed."""
        fake_result = [(2, 1, 6, "", ("93.184.216.34", 0))]
        with patch("shoreguard.config.socket.getaddrinfo", return_value=fake_result):
            assert _is_private_ip("example.com") is False

    def test_hostname_dns_failure(self):
        """DNS failure for unknown hostname returns False (will fail at connect)."""
        import socket

        with patch(
            "shoreguard.config.socket.getaddrinfo",
            side_effect=socket.gaierror("Name resolution failed"),
        ):
            assert _is_private_ip("nonexistent.invalid") is False

    def test_hostname_dns_timeout(self):
        """DNS timeout returns False."""

        with patch(
            "shoreguard.config.socket.getaddrinfo",
            side_effect=TimeoutError("timed out"),
        ):
            assert _is_private_ip("slow-dns.example.com") is False


# ─── _validate_endpoint_format ───────────────────────────────────────────────


class TestValidateEndpointFormat:
    def test_valid_ip_port(self):
        _validate_endpoint_format("8.8.8.8:8443")

    def test_valid_hostname_port(self):
        with patch("shoreguard.api.routes.gateway.is_private_ip", return_value=False):
            _validate_endpoint_format("gateway.example.com:443")

    def test_missing_port(self):
        with pytest.raises(ValueError, match="host:port"):
            _validate_endpoint_format("just-a-host")

    def test_port_zero(self):
        with pytest.raises(ValueError, match="between 1 and 65535"):
            _validate_endpoint_format("8.8.8.8:0")

    def test_port_negative_via_format(self):
        """Negative ports don't match the regex."""
        with pytest.raises(ValueError, match="host:port"):
            _validate_endpoint_format("8.8.8.8:-1")

    def test_port_exceeds_max(self):
        with pytest.raises(ValueError, match="between 1 and 65535"):
            _validate_endpoint_format("8.8.8.8:70000")

    def test_port_boundary_1(self):
        _validate_endpoint_format("8.8.8.8:1")

    def test_port_boundary_65535(self):
        _validate_endpoint_format("8.8.8.8:65535")

    def test_private_ip_rejected(self):
        with pytest.raises(ValueError, match="private"):
            _validate_endpoint_format("192.168.1.1:8443")

    def test_loopback_rejected(self):
        with pytest.raises(ValueError, match="private"):
            _validate_endpoint_format("127.0.0.1:8443")

    def test_url_format_rejected(self):
        with pytest.raises(ValueError, match="host:port"):
            _validate_endpoint_format("http://host:443")

    def test_empty_host(self):
        with pytest.raises(ValueError, match="host:port"):
            _validate_endpoint_format(":8443")

    def test_multiple_colons(self):
        with pytest.raises(ValueError, match="host:port"):
            _validate_endpoint_format("host:port:extra")


# ─── CreateGatewayRequest.validate_port ──────────────────────────────────────


class TestCreateGatewayRequestPort:
    def test_valid_port(self):
        from shoreguard.api.routes.gateway import CreateGatewayRequest

        req = CreateGatewayRequest(name="gw", port=8443)
        assert req.port == 8443

    def test_port_none(self):
        from shoreguard.api.routes.gateway import CreateGatewayRequest

        req = CreateGatewayRequest(name="gw", port=None)
        assert req.port is None

    def test_port_zero_rejected(self):
        from pydantic import ValidationError

        from shoreguard.api.routes.gateway import CreateGatewayRequest

        with pytest.raises(ValidationError, match="between 1 and 65535"):
            CreateGatewayRequest(name="gw", port=0)

    def test_port_negative_rejected(self):
        from pydantic import ValidationError

        from shoreguard.api.routes.gateway import CreateGatewayRequest

        with pytest.raises(ValidationError, match="between 1 and 65535"):
            CreateGatewayRequest(name="gw", port=-1)

    def test_port_exceeds_max_rejected(self):
        from pydantic import ValidationError

        from shoreguard.api.routes.gateway import CreateGatewayRequest

        with pytest.raises(ValidationError, match="between 1 and 65535"):
            CreateGatewayRequest(name="gw", port=65536)

    def test_port_boundary_1(self):
        from shoreguard.api.routes.gateway import CreateGatewayRequest

        req = CreateGatewayRequest(name="gw", port=1)
        assert req.port == 1

    def test_port_boundary_65535(self):
        from shoreguard.api.routes.gateway import CreateGatewayRequest

        req = CreateGatewayRequest(name="gw", port=65535)
        assert req.port == 65535


# ─── validate_webhook_url ────────────────────────────────────────────────────


class TestValidateWebhookUrl:
    def test_valid_https_url(self):
        with patch("shoreguard.api.validation.is_private_ip", return_value=False):
            result = validate_webhook_url("https://hooks.example.com/endpoint")
        assert result == "https://hooks.example.com/endpoint"

    def test_valid_http_url(self):
        with patch("shoreguard.api.validation.is_private_ip", return_value=False):
            result = validate_webhook_url("http://hooks.example.com/endpoint")
        assert result == "http://hooks.example.com/endpoint"

    def test_strips_whitespace(self):
        with patch("shoreguard.api.validation.is_private_ip", return_value=False):
            result = validate_webhook_url("  https://hooks.example.com  ")
        assert result == "https://hooks.example.com"

    def test_file_scheme_rejected(self):
        with pytest.raises(DomainValidationError, match="http or https"):
            validate_webhook_url("file:///etc/passwd")

    def test_ftp_scheme_rejected(self):
        with pytest.raises(DomainValidationError, match="http or https"):
            validate_webhook_url("ftp://files.example.com/data")

    def test_no_scheme_rejected(self):
        with pytest.raises(DomainValidationError, match="http or https"):
            validate_webhook_url("example.com/webhook")

    def test_no_hostname_rejected(self):
        with pytest.raises(DomainValidationError, match="hostname"):
            validate_webhook_url("http://")

    def test_credentials_in_url_rejected(self):
        with pytest.raises(DomainValidationError, match="credentials"):
            validate_webhook_url("https://user:pass@example.com/webhook")

    def test_username_only_rejected(self):
        with pytest.raises(DomainValidationError, match="credentials"):
            validate_webhook_url("https://user@example.com/webhook")

    def test_private_ip_rejected(self):
        with pytest.raises(DomainValidationError, match="private"):
            validate_webhook_url("https://192.168.1.1:8080/hook")

    def test_loopback_rejected(self):
        with pytest.raises(DomainValidationError, match="private"):
            validate_webhook_url("https://127.0.0.1/hook")

    def test_localhost_rejected(self):
        with pytest.raises(DomainValidationError, match="private"):
            validate_webhook_url("https://localhost/hook")

    def test_private_ip_allowed_in_local_mode(self):
        with patch("shoreguard.api.validation.get_settings") as mock_settings:
            mock_settings.return_value.server.local_mode = True
            result = validate_webhook_url("https://192.168.1.1/hook")
        assert result == "https://192.168.1.1/hook"

    def test_cloud_metadata_rejected(self):
        """AWS/GCP metadata endpoint (link-local) must be blocked."""
        with pytest.raises(DomainValidationError, match="private"):
            validate_webhook_url("http://169.254.169.254/latest/meta-data/")


class TestValidateSmtpHost:
    def test_public_host_allowed(self):
        with patch("shoreguard.api.validation.is_private_ip", return_value=False):
            validate_smtp_host("smtp.gmail.com")  # should not raise

    def test_private_host_rejected(self):
        with pytest.raises(DomainValidationError, match="private"):
            validate_smtp_host("192.168.1.1")

    def test_localhost_rejected(self):
        with pytest.raises(DomainValidationError, match="private"):
            validate_smtp_host("localhost")
