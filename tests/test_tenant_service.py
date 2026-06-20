"""Tests for the tenant grouping service."""

from __future__ import annotations

import datetime

import pytest

from shoreguard.models import Gateway, User


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _seed(container, *, gateways: list[str], users: list[str]) -> dict[str, int]:
    """Insert gateways and users, returning user-email -> id."""
    ids: dict[str, int] = {}
    async with container.async_session_factory() as session:
        for name in gateways:
            session.add(Gateway(name=name, endpoint=f"{name}:50051", registered_at=_now()))
        for email in users:
            session.add(User(email=email, role="viewer", is_active=True, created_at=_now()))
        await session.commit()
        for email in users:
            from sqlalchemy import select

            uid = (await session.execute(select(User.id).where(User.email == email))).scalar_one()
            ids[email] = uid
    return ids


async def test_tenant_crud_and_duplicate(container) -> None:
    svc = container.tenant
    t = await svc.create_tenant("team-a", "A team")
    assert t["name"] == "team-a"
    assert (await svc.get_tenant(t["id"]))["description"] == "A team"
    listed = await svc.list_tenants()
    assert listed[0]["name"] == "team-a"
    assert listed[0]["gateway_count"] == 0 and listed[0]["user_count"] == 0
    updated = await svc.update_tenant(t["id"], name="team-a2", description=None)
    assert updated["name"] == "team-a2"
    with pytest.raises(ValueError):
        await svc.create_tenant("team-a2", None)  # duplicate name
    assert await svc.delete_tenant(t["id"]) is True
    assert await svc.delete_tenant(t["id"]) is False


async def test_membership_reflected_in_get_and_counts(container) -> None:
    svc = container.tenant
    ids = await _seed(container, gateways=["gw1", "gw2"], users=["a@x.io"])
    t = await svc.create_tenant("team-b", None)
    assert await svc.add_gateway(t["id"], "gw1") is True
    assert await svc.add_gateway(t["id"], "gw1") is True  # idempotent
    assert await svc.add_user(t["id"], ids["a@x.io"]) is True
    detail = await svc.get_tenant(t["id"])
    assert detail["gateways"] == ["gw1"]
    assert detail["users"] == [{"id": ids["a@x.io"], "email": "a@x.io"}]
    counts = (await svc.list_tenants())[0]
    assert counts["gateway_count"] == 1 and counts["user_count"] == 1
    # Unknown gateway/user → False
    assert await svc.add_gateway(t["id"], "nope") is False
    assert await svc.add_user(t["id"], 9999) is False
    # Removal
    assert await svc.remove_gateway(t["id"], "gw1") is True
    assert await svc.remove_gateway(t["id"], "gw1") is False
    assert await svc.remove_user(t["id"], ids["a@x.io"]) is True


async def test_scoped_gateway_names_for_user(container) -> None:
    svc = container.tenant
    ids = await _seed(container, gateways=["gw1", "gw2", "gw3"], users=["u@x.io", "none@x.io"])
    t1 = await svc.create_tenant("t1", None)
    t2 = await svc.create_tenant("t2", None)
    await svc.add_gateway(t1["id"], "gw1")
    await svc.add_gateway(t2["id"], "gw2")
    await svc.add_user(t1["id"], ids["u@x.io"])
    await svc.add_user(t2["id"], ids["u@x.io"])
    # User in two tenants → union of their gateways.
    assert await svc.scoped_gateway_names_for_user(ids["u@x.io"]) == {"gw1", "gw2"}
    # User in no tenant → None (full fleet).
    assert await svc.scoped_gateway_names_for_user(ids["none@x.io"]) is None
    # User in a tenant that holds no gateways → empty set (sees nothing).
    t3 = await svc.create_tenant("t3", None)
    await svc.add_user(t3["id"], ids["none@x.io"])
    assert await svc.scoped_gateway_names_for_user(ids["none@x.io"]) == set()


async def test_rollup_filters_to_tenant_gateways(container) -> None:
    svc = container.tenant
    await _seed(container, gateways=["gw1", "gw2"], users=[])
    t = await svc.create_tenant("t", None)
    await svc.add_gateway(t["id"], "gw1")
    rollup = await svc.rollup(t["id"], container.budget, days=7)
    assert rollup["gateways"] == ["gw1"]
    assert "spend" in rollup and "top" in rollup["spend"]
