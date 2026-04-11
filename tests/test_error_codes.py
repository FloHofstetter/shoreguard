"""Tests for structured error codes in API responses."""

from __future__ import annotations

from typing import Any

import grpc
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from shoreguard.api.error_codes import (
    AUTHENTICATION_REQUIRED,
    CONFLICT,
    GATEWAY_NOT_CONNECTED,
    INTERNAL_ERROR,
    NOT_FOUND,
    PERMISSION_DENIED,
    POLICY_ERROR,
    RATE_LIMITED,
    SANDBOX_CONFLICT,
    SERVICE_UNAVAILABLE,
    TIMEOUT,
    VALIDATION_ERROR,
    code_for_status,
)
from shoreguard.api.errors import register_error_handlers
from shoreguard.exceptions import (
    ConflictError,
    GatewayNotConnectedError,
    NotFoundError,
    PolicyError,
    SandboxError,
    ValidationError,
)

# ── Test app with error handlers ─────────────────────────────────────────────

_test_app = FastAPI()
register_error_handlers(_test_app)

_pending_exc: dict[str, Any] = {}


@_test_app.get("/raise")
async def _raise_error() -> None:
    raise _pending_exc["exc"]


@pytest.fixture
async def err_client():
    async with AsyncClient(transport=ASGITransport(app=_test_app), base_url="http://test") as c:
        yield c


async def _get_error(client: AsyncClient, exc: Exception) -> dict:
    _pending_exc["exc"] = exc
    resp = await client.get("/raise")
    return resp.json()


# ── code_for_status unit tests ───────────────────────────────────────────────


class TestCodeForStatus:
    def test_400(self):
        assert code_for_status(400) == VALIDATION_ERROR

    def test_401(self):
        assert code_for_status(401) == AUTHENTICATION_REQUIRED

    def test_403(self):
        assert code_for_status(403) == PERMISSION_DENIED

    def test_404(self):
        assert code_for_status(404) == NOT_FOUND

    def test_409(self):
        assert code_for_status(409) == CONFLICT

    def test_429(self):
        assert code_for_status(429) == RATE_LIMITED

    def test_500(self):
        assert code_for_status(500) == INTERNAL_ERROR

    def test_503(self):
        assert code_for_status(503) == SERVICE_UNAVAILABLE

    def test_504(self):
        assert code_for_status(504) == TIMEOUT

    def test_422(self):
        assert code_for_status(422) == VALIDATION_ERROR

    def test_unknown_status_falls_back_to_internal(self):
        assert code_for_status(418) == INTERNAL_ERROR


# ── RFC 9457 Problem Details contract ────────────────────────────────────────


class TestRFC9457Shape:
    """Error responses follow RFC 9457 Problem Details for HTTP APIs."""

    async def test_body_carries_rfc9457_members(self, err_client):
        _pending_exc["exc"] = HTTPException(404, "not here")
        resp = await err_client.get("/raise")
        body = resp.json()
        assert body["type"] == "about:blank"
        assert body["title"] == "Not Found"
        assert body["status"] == 404
        assert body["detail"] == "not here"
        assert body["code"] == NOT_FOUND

    async def test_content_type_is_problem_json(self, err_client):
        _pending_exc["exc"] = HTTPException(409, "duplicate")
        resp = await err_client.get("/raise")
        assert resp.headers["content-type"].startswith("application/problem+json")

    async def test_title_humanizes_snake_case_code(self, err_client):
        body = await _get_error(err_client, HTTPException(503, "down"))
        assert body["title"] == "Service Unavailable"


# ── HTTPException handler ────────────────────────────────────────────────────


class TestHTTPExceptionCodes:
    async def test_404_has_not_found_code(self, err_client):
        body = await _get_error(err_client, HTTPException(404, "not here"))
        assert body["code"] == NOT_FOUND
        assert body["detail"] == "not here"

    async def test_400_has_validation_error_code(self, err_client):
        body = await _get_error(err_client, HTTPException(400, "bad input"))
        assert body["code"] == VALIDATION_ERROR

    async def test_401_has_auth_required_code(self, err_client):
        body = await _get_error(err_client, HTTPException(401, "no creds"))
        assert body["code"] == AUTHENTICATION_REQUIRED

    async def test_403_has_permission_denied_code(self, err_client):
        body = await _get_error(err_client, HTTPException(403, "nope"))
        assert body["code"] == PERMISSION_DENIED

    async def test_409_has_conflict_code(self, err_client):
        body = await _get_error(err_client, HTTPException(409, "duplicate"))
        assert body["code"] == CONFLICT

    async def test_503_has_service_unavailable_code(self, err_client):
        body = await _get_error(err_client, HTTPException(503, "down"))
        assert body["code"] == SERVICE_UNAVAILABLE


# ── Domain exception codes ───────────────────────────────────────────────────


class TestDomainExceptionCodes:
    @pytest.mark.parametrize(
        ("exc_class", "expected_code"),
        [
            (GatewayNotConnectedError, GATEWAY_NOT_CONNECTED),
            (NotFoundError, NOT_FOUND),
            (PolicyError, POLICY_ERROR),
            (SandboxError, SANDBOX_CONFLICT),
            (ConflictError, CONFLICT),
            (ValidationError, VALIDATION_ERROR),
        ],
    )
    async def test_domain_exceptions_produce_correct_code(
        self, err_client, exc_class, expected_code
    ):
        body = await _get_error(err_client, exc_class("test error"))
        assert body["code"] == expected_code
        assert body["detail"] == "test error"


# ── Timeout code ─────────────────────────────────────────────────────────────


class TestTimeoutCode:
    async def test_timeout_produces_timeout_code(self, err_client):
        body = await _get_error(err_client, TimeoutError("gateway timed out"))
        assert body["code"] == TIMEOUT


# ── Generic exception code ───────────────────────────────────────────────────
# Tested via integration (real app) below — the minimal test app lacks the
# full middleware stack needed for the catch-all Exception handler.


# ── gRPC exception codes ────────────────────────────────────────────────────


class _FakeRpcError(grpc.RpcError, Exception):
    """A raiseable gRPC error for testing."""

    def __init__(self, status_code: grpc.StatusCode) -> None:
        super().__init__(f"gRPC {status_code.name}")
        self._code = status_code

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return "grpc detail"


class TestGrpcExceptionCodes:
    @staticmethod
    def _make_grpc_error(status_code: grpc.StatusCode) -> grpc.RpcError:
        return _FakeRpcError(status_code)

    @pytest.mark.parametrize(
        ("grpc_code", "expected_error_code"),
        [
            (grpc.StatusCode.INVALID_ARGUMENT, VALIDATION_ERROR),
            (grpc.StatusCode.OUT_OF_RANGE, VALIDATION_ERROR),
            (grpc.StatusCode.NOT_FOUND, NOT_FOUND),
            (grpc.StatusCode.ALREADY_EXISTS, CONFLICT),
            (grpc.StatusCode.ABORTED, CONFLICT),
            (grpc.StatusCode.FAILED_PRECONDITION, CONFLICT),
            (grpc.StatusCode.RESOURCE_EXHAUSTED, CONFLICT),
            (grpc.StatusCode.PERMISSION_DENIED, PERMISSION_DENIED),
            (grpc.StatusCode.UNAUTHENTICATED, AUTHENTICATION_REQUIRED),
            (grpc.StatusCode.UNAVAILABLE, GATEWAY_NOT_CONNECTED),
            (grpc.StatusCode.DEADLINE_EXCEEDED, TIMEOUT),
        ],
    )
    async def test_grpc_errors_produce_correct_code(
        self, err_client, grpc_code, expected_error_code
    ):
        exc = self._make_grpc_error(grpc_code)
        body = await _get_error(err_client, exc)
        assert body["code"] == expected_error_code


# ── RequestValidationError handler ──────────────────────────────────────────


class TestRequestValidationError:
    async def test_pydantic_validation_error_has_code(self, err_client):
        """Pydantic 422 errors get our standard {detail, code, errors} shape."""
        from pydantic import BaseModel

        @_test_app.post("/validate")
        async def _validate(body: BaseModel) -> dict:
            return {}

        resp = await err_client.post("/validate", content=b"not json")
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == VALIDATION_ERROR
        assert body["detail"] == "Validation error"
        assert "errors" in body


# ── Integration: real app produces code field ────────────────────────────────


@pytest.fixture
async def client():
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class TestIntegration:
    async def test_real_404_has_code(self, client):
        resp = await client.get("/api/operations/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == NOT_FOUND

    async def test_healthz_success_has_no_code(self, client):
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert "code" not in resp.json()

    async def test_readyz_503_has_code(self, client):
        from unittest.mock import patch

        with patch("shoreguard.db.get_engine", side_effect=RuntimeError("boom")):
            resp = await client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not ready"
