"""Unit tests for ProviderProfileManager — FakeStub pattern."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shoreguard.client._proto import openshell_pb2
from shoreguard.client.provider_profiles import (
    ProviderProfileManager,
    _profile_to_dict,
)


def _make_profile(profile_id: str = "claude") -> openshell_pb2.ProviderProfile:
    cred = openshell_pb2.ProviderProfileCredential(
        name="api_key",
        description="Anthropic API key",
        env_vars=["ANTHROPIC_API_KEY"],
        required=True,
        auth_style="bearer",
    )
    return openshell_pb2.ProviderProfile(
        id=profile_id,
        display_name=profile_id.title(),
        description="…",
        category=openshell_pb2.PROVIDER_PROFILE_CATEGORY_INFERENCE,
        credentials=[cred],
        inference_capable=True,
    )


class _FakeStub:
    def __init__(self) -> None:
        self.request = None

    async def ListProviderProfiles(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(profiles=[_make_profile("claude"), _make_profile("openai")])

    async def GetProviderProfile(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(profile=_make_profile(req.id))

    async def LintProviderProfiles(self, req, timeout=None):
        self.request = req
        diag = openshell_pb2.ProviderProfileDiagnostic(
            source="inline",
            profile_id="oops",
            field="display_name",
            message="empty",
            severity="error",
        )
        return SimpleNamespace(valid=False, diagnostics=[diag])

    async def ImportProviderProfiles(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            imported=True,
            profiles=[_make_profile("claude")],
            diagnostics=[],
        )

    async def DeleteProviderProfile(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(deleted=True)


@pytest.fixture
def stub() -> _FakeStub:
    return _FakeStub()


@pytest.fixture
def mgr(stub: _FakeStub) -> ProviderProfileManager:
    m = object.__new__(ProviderProfileManager)
    m._stub = stub  # type: ignore[assignment]
    m._timeout = 30.0
    return m


async def test_list_passes_pagination(mgr: ProviderProfileManager, stub: _FakeStub) -> None:
    out = await mgr.list(limit=42, offset=7)
    assert stub.request is not None
    assert stub.request.limit == 42
    assert stub.request.offset == 7
    assert [p["id"] for p in out] == ["claude", "openai"]


async def test_get_returns_dict(mgr: ProviderProfileManager, stub: _FakeStub) -> None:
    out = await mgr.get("claude")
    assert stub.request is not None
    assert stub.request.id == "claude"
    assert out["id"] == "claude"
    assert out["category"] == "inference"
    assert out["inference_capable"] is True
    assert out["credentials"][0]["env_vars"] == ["ANTHROPIC_API_KEY"]


async def test_lint_round_trip(mgr: ProviderProfileManager, stub: _FakeStub) -> None:
    result = await mgr.lint([{"profile": {"id": "oops", "display_name": ""}, "source": "inline"}])
    assert result["valid"] is False
    assert result["diagnostics"][0]["message"] == "empty"


async def test_import_returns_imported_profiles(
    mgr: ProviderProfileManager, stub: _FakeStub
) -> None:
    result = await mgr.import_(
        [{"profile": {"id": "claude", "display_name": "Claude"}, "source": "inline"}]
    )
    assert result["imported"] is True
    assert result["profiles"][0]["id"] == "claude"


async def test_delete_returns_bool(mgr: ProviderProfileManager, stub: _FakeStub) -> None:
    assert await mgr.delete("claude") is True
    assert stub.request is not None
    assert stub.request.id == "claude"


async def test_profile_to_dict_unknown_category() -> None:
    """An unseen category integer is passed through as a raw int."""
    profile = openshell_pb2.ProviderProfile(id="x", category=99)  # type: ignore[arg-type]
    d = _profile_to_dict(profile)
    assert d["category"] == 99
