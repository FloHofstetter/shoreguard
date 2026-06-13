"""Tests for tenant visibility scoping (rbac primitive + read-path threading)."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

from shoreguard.api import auth
from shoreguard.api.auth.rbac import scoped_gateway_names
from shoreguard.models import AuditEntry, Gateway, User


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _request(*, role: str | None, user_db_id: int | None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(role=role, user_db_id=user_db_id))


async def _seed_user_in_tenant(container, email: str, gateways: list[str]) -> int:
    from sqlalchemy import select

    async with container.async_session_factory() as session:
        for name in gateways:
            session.add(Gateway(name=name, endpoint=f"{name}:1", registered_at=_now()))
        session.add(User(email=email, role="viewer", is_active=True, created_at=_now()))
        await session.commit()
        uid = (await session.execute(select(User.id).where(User.email == email))).scalar_one()
    t = await container.tenant.create_tenant("scoped", None)
    for name in gateways:
        await container.tenant.add_gateway(t["id"], name)
    await container.tenant.add_user(t["id"], uid)
    return uid


async def test_no_auth_is_unscoped(container) -> None:
    auth.state.no_auth = True  # set by the autouse fixture, but be explicit
    assert await scoped_gateway_names(_request(role="viewer", user_db_id=1)) is None


async def test_admin_and_disabled_are_unscoped(container, monkeypatch) -> None:
    auth.state.no_auth = False
    auth.state.session_factory = container.async_session_factory
    # Admins always see the full fleet.
    assert await scoped_gateway_names(_request(role="admin", user_db_id=1)) is None
    # Disabling the feature yields the full fleet even for a non-admin.
    monkeypatch.setenv("SHOREGUARD_TENANT_ENABLED", "false")
    from shoreguard.settings import reset_settings

    reset_settings()
    assert await scoped_gateway_names(_request(role="viewer", user_db_id=1)) is None


async def test_user_scoped_and_unassigned(container) -> None:
    auth.state.no_auth = False
    auth.state.session_factory = container.async_session_factory
    uid = await _seed_user_in_tenant(container, "u@x.io", ["gw1", "gw2"])
    assert await scoped_gateway_names(_request(role="viewer", user_db_id=uid)) == {"gw1", "gw2"}
    # A user in no tenant falls open to the full fleet.
    assert await scoped_gateway_names(_request(role="viewer", user_db_id=999)) is None
    # A service principal / unidentified caller is not tenant-scoped.
    assert await scoped_gateway_names(_request(role="viewer", user_db_id=None)) is None


async def test_digest_scope_keeps_unattributed_audit(container) -> None:
    # Two gateways' audit events plus a NULL-gateway (cross-cutting) event.
    async with container.async_session_factory() as session:
        session.add(Gateway(name="gw1", endpoint="gw1:1", registered_at=_now()))
        session.add(Gateway(name="gw2", endpoint="gw2:1", registered_at=_now()))
        session.add(
            AuditEntry(
                timestamp=_now(),
                actor="x",
                actor_role="admin",
                action="sandbox.create",
                resource_type="sandbox",
                resource_id="a",
                gateway_name="gw1",
            )
        )
        session.add(
            AuditEntry(
                timestamp=_now(),
                actor="x",
                actor_role="admin",
                action="sandbox.create",
                resource_type="sandbox",
                resource_id="b",
                gateway_name="gw2",
            )
        )
        session.add(
            AuditEntry(
                timestamp=_now(),
                actor="x",
                actor_role="admin",
                action="auth.forbidden",
                resource_type="role",
                resource_id="admin",
                gateway_name=None,  # cross-cutting / unattributed
            )
        )
        await session.commit()

    scoped = await container.digest.build(scope={"gw1"})
    by_action = scoped["audit"]["by_action"]
    # gw1's create counts, gw2's does not; the NULL-gateway forbidden event stays.
    assert by_action.get("sandbox.create") == 1
    assert scoped["audit"]["forbidden"] == 1
    assert scoped["gateways"]["total"] == 1

    full = await container.digest.build()
    assert full["audit"]["by_action"].get("sandbox.create") == 2
    assert full["gateways"]["total"] == 2
