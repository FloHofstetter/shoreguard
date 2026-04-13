"""Unit tests for BootHookService — pre/post hook registration, ordering, and execution."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.exceptions import BootHookError, ValidationError
from shoreguard.models import Base
from shoreguard.services.boot_hooks import BootHookService


@pytest.fixture
def svc_with_provider():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    sandbox_service = MagicMock()
    sandbox_service.exec.return_value = {"stdout": "ok\n", "stderr": "", "exit_code": 0}
    provider = MagicMock(return_value=sandbox_service)
    svc = BootHookService(factory, sandbox_service_provider=provider)
    yield svc, provider, sandbox_service
    engine.dispose()


@pytest.fixture
def svc(svc_with_provider):
    return svc_with_provider[0]


def _make(svc: BootHookService, **kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "gateway_name": "gw1",
        "sandbox_name": "sb1",
        "name": "h",
        "phase": "post_create",
        "command": "echo hi",
        "actor": "admin@test.com",
    }
    defaults.update(kwargs)
    return svc.create(**defaults)


class TestCreate:
    def test_create_basic(self, svc):
        hook = _make(svc, name="warm", command="echo go")
        assert hook["id"] > 0
        assert hook["name"] == "warm"
        assert hook["command"] == "echo go"
        assert hook["phase"] == "post_create"
        assert hook["enabled"] is True
        assert hook["order"] == 0

    def test_create_assigns_sequential_order(self, svc):
        a = _make(svc, name="a")
        b = _make(svc, name="b")
        c = _make(svc, name="c")
        assert (a["order"], b["order"], c["order"]) == (0, 1, 2)

    def test_create_independent_phases(self, svc):
        a = _make(svc, name="pre", phase="pre_create", command="true")
        b = _make(svc, name="post", phase="post_create", command="true")
        assert a["order"] == 0
        assert b["order"] == 0

    def test_create_with_env(self, svc):
        hook = _make(svc, name="e", env={"FOO": "bar"})
        assert hook["env"] == {"FOO": "bar"}

    def test_reject_empty_name(self, svc):
        with pytest.raises(ValidationError):
            _make(svc, name="   ")

    def test_reject_empty_command(self, svc):
        with pytest.raises(ValidationError):
            _make(svc, command="")

    def test_reject_invalid_phase(self, svc):
        with pytest.raises(ValidationError):
            _make(svc, phase="boot")

    def test_reject_zero_timeout(self, svc):
        with pytest.raises(ValidationError):
            _make(svc, timeout_seconds=0)

    def test_reject_huge_timeout(self, svc):
        with pytest.raises(ValidationError):
            _make(svc, timeout_seconds=9999)


class TestList:
    def test_list_empty(self, svc):
        assert svc.list("gw1", "sb1") == []

    def test_list_returns_all(self, svc):
        _make(svc, name="a", phase="pre_create", command="true")
        _make(svc, name="b", phase="post_create")
        items = svc.list("gw1", "sb1")
        assert {h["name"] for h in items} == {"a", "b"}

    def test_list_phase_filter(self, svc):
        _make(svc, name="a", phase="pre_create", command="true")
        _make(svc, name="b", phase="post_create")
        items = svc.list("gw1", "sb1", phase="post_create")
        assert [h["name"] for h in items] == ["b"]

    def test_list_phase_validation(self, svc):
        with pytest.raises(ValidationError):
            svc.list("gw1", "sb1", phase="bogus")

    def test_list_scoped_by_sandbox(self, svc):
        _make(svc, sandbox_name="sb1", name="a")
        _make(svc, sandbox_name="sb2", name="b")
        items = svc.list("gw1", "sb1")
        assert [h["name"] for h in items] == ["a"]


class TestUpdate:
    def test_update_command(self, svc):
        hook = _make(svc)
        result = svc.update(hook["id"], command="echo new")
        assert result["command"] == "echo new"

    def test_update_enabled(self, svc):
        hook = _make(svc)
        result = svc.update(hook["id"], enabled=False)
        assert result["enabled"] is False

    def test_update_unknown(self, svc):
        assert svc.update(999, command="true") is None

    def test_update_reject_empty_command(self, svc):
        hook = _make(svc)
        with pytest.raises(ValidationError):
            svc.update(hook["id"], command="")


class TestDelete:
    def test_delete(self, svc):
        hook = _make(svc)
        assert svc.delete(hook["id"]) is True
        assert svc.get(hook["id"]) is None

    def test_delete_unknown(self, svc):
        assert svc.delete(123) is False

    def test_delete_for_sandbox(self, svc):
        _make(svc, name="a")
        _make(svc, name="b")
        _make(svc, sandbox_name="sb2", name="c")
        removed = svc.delete_for_sandbox("gw1", "sb1")
        assert removed == 2
        assert [h["name"] for h in svc.list("gw1", "sb1")] == []
        assert [h["name"] for h in svc.list("gw1", "sb2")] == ["c"]


class TestReorder:
    def test_reorder(self, svc):
        a = _make(svc, name="a")
        b = _make(svc, name="b")
        c = _make(svc, name="c")
        result = svc.reorder("gw1", "sb1", "post_create", [c["id"], a["id"], b["id"]])
        assert [h["name"] for h in result] == ["c", "a", "b"]
        assert [h["order"] for h in result] == [0, 1, 2]

    def test_reorder_mismatch_raises(self, svc):
        a = _make(svc, name="a")
        with pytest.raises(ValidationError):
            svc.reorder("gw1", "sb1", "post_create", [a["id"], 999])

    def test_reorder_invalid_phase(self, svc):
        with pytest.raises(ValidationError):
            svc.reorder("gw1", "sb1", "boot", [])


class TestRunPreCreate:
    def test_run_pre_create_success(self, svc):
        _make(svc, name="ok", phase="pre_create", command="/bin/true")
        results = svc.run_pre_create("gw1", "sb1", {"name": "sb1", "image": "img"})
        assert len(results) == 1
        assert results[0]["status"] == "success"

    def test_run_pre_create_failure_raises(self, svc):
        _make(svc, name="bad", phase="pre_create", command="/bin/false")
        with pytest.raises(BootHookError) as exc:
            svc.run_pre_create("gw1", "sb1", {"name": "sb1"})
        assert exc.value.hook_name == "bad"
        assert exc.value.phase == "pre_create"

    def test_run_pre_create_invalid_command(self, svc):
        _make(svc, name="bad", phase="pre_create", command='echo "unterminated')
        with pytest.raises(BootHookError):
            svc.run_pre_create("gw1", "sb1", {})

    def test_run_pre_create_skips_disabled(self, svc):
        hook = _make(svc, name="disabled", phase="pre_create", command="/bin/false")
        svc.update(hook["id"], enabled=False)
        results = svc.run_pre_create("gw1", "sb1", {})
        assert results == []

    def test_run_pre_create_command_not_found(self, svc):
        _make(
            svc,
            name="missing",
            phase="pre_create",
            command="/this/does/not/exist/at/all",
        )
        with pytest.raises(BootHookError):
            svc.run_pre_create("gw1", "sb1", {})

    def test_run_pre_create_persists_status(self, svc):
        hook = _make(svc, name="ok", phase="pre_create", command="/bin/true")
        svc.run_pre_create("gw1", "sb1", {})
        refreshed = svc.get(hook["id"])
        assert refreshed["last_status"] == "success"
        assert refreshed["last_run_at"] is not None

    def test_run_pre_create_timeout(self, svc, monkeypatch):
        _make(
            svc,
            name="slow",
            phase="pre_create",
            command="/bin/sleep 10",
            timeout_seconds=1,
        )

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

        monkeypatch.setattr("shoreguard.services.boot_hooks.subprocess.run", fake_run)
        with pytest.raises(BootHookError) as exc:
            svc.run_pre_create("gw1", "sb1", {})
        assert "timeout" in str(exc.value).lower()


class TestRunPostCreate:
    def test_run_post_create_success(self, svc_with_provider):
        svc, provider, sandbox_service = svc_with_provider
        _make(svc, name="ok", phase="post_create", command="echo done")
        results = svc.run_post_create("gw1", "sb1")
        assert len(results) == 1
        assert results[0]["status"] == "success"
        sandbox_service.exec.assert_called_once()
        provider.assert_called_with("gw1")

    def test_run_post_create_halts_on_failure(self, svc_with_provider):
        svc, _provider, sandbox_service = svc_with_provider
        sandbox_service.exec.side_effect = [
            {"stdout": "", "stderr": "boom", "exit_code": 1},
        ]
        _make(svc, name="bad", phase="post_create", command="false")
        _make(svc, name="next", phase="post_create", command="true")
        results = svc.run_post_create("gw1", "sb1")
        assert len(results) == 1
        assert results[0]["status"] == "failure"

    def test_run_post_create_continue_on_failure(self, svc_with_provider):
        svc, _provider, sandbox_service = svc_with_provider
        sandbox_service.exec.side_effect = [
            {"stdout": "", "stderr": "boom", "exit_code": 1},
            {"stdout": "ok", "stderr": "", "exit_code": 0},
        ]
        _make(
            svc,
            name="soft",
            phase="post_create",
            command="false",
            continue_on_failure=True,
        )
        _make(svc, name="next", phase="post_create", command="true")
        results = svc.run_post_create("gw1", "sb1")
        assert [r["status"] for r in results] == ["failure", "success"]

    def test_run_post_create_no_provider(self, svc_with_provider):
        svc, provider, _sandbox_service = svc_with_provider
        provider.return_value = None
        _make(svc, name="ok", phase="post_create", command="true")
        assert svc.run_post_create("gw1", "sb1") == []

    def test_run_post_create_skips_disabled(self, svc_with_provider):
        svc, _provider, sandbox_service = svc_with_provider
        hook = _make(svc, name="off", phase="post_create", command="true")
        svc.update(hook["id"], enabled=False)
        results = svc.run_post_create("gw1", "sb1")
        assert results == []
        sandbox_service.exec.assert_not_called()

    def test_run_post_create_validation_error(self, svc_with_provider):
        svc, _provider, sandbox_service = svc_with_provider
        sandbox_service.exec.side_effect = ValidationError("bad command")
        _make(svc, name="x", phase="post_create", command="echo hi")
        results = svc.run_post_create("gw1", "sb1")
        assert results[0]["status"] == "failure"


class TestRunOne:
    def test_run_one_pre(self, svc):
        hook = _make(svc, name="ok", phase="pre_create", command="/bin/true")
        result = svc.run_one(hook["id"])
        assert result["status"] == "success"

    def test_run_one_post(self, svc_with_provider):
        svc, _provider, sandbox_service = svc_with_provider
        hook = _make(svc, name="ok", phase="post_create", command="echo")
        result = svc.run_one(hook["id"])
        assert result["status"] == "success"
        sandbox_service.exec.assert_called_once()

    def test_run_one_unknown(self, svc):
        assert svc.run_one(9999) is None
