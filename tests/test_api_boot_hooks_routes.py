"""Integration tests for the boot hooks API routes (M22)."""

from __future__ import annotations

GW = "test"
SB = "sb1"
BASE = f"/api/gateways/{GW}/sandboxes/{SB}/hooks"


def _create_body(**overrides) -> dict:
    body = {
        "name": "warm",
        "phase": "post_create",
        "command": "echo hi",
    }
    body.update(overrides)
    return body


class TestCreate:
    async def test_create_minimal(self, api_client):
        resp = await api_client.post(BASE, json=_create_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "warm"
        assert data["phase"] == "post_create"
        assert data["enabled"] is True
        assert data["order"] == 0

    async def test_create_full(self, api_client):
        resp = await api_client.post(
            BASE,
            json=_create_body(
                name="seed",
                phase="pre_create",
                command="/bin/true",
                workdir="/tmp",
                env={"FOO": "bar"},
                timeout_seconds=5,
                enabled=False,
                continue_on_failure=True,
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["env"] == {"FOO": "bar"}
        assert data["timeout_seconds"] == 5
        assert data["enabled"] is False
        assert data["continue_on_failure"] is True

    async def test_create_invalid_phase(self, api_client):
        resp = await api_client.post(BASE, json=_create_body(phase="boot"))
        assert resp.status_code == 422

    async def test_create_duplicate_name(self, api_client):
        await api_client.post(BASE, json=_create_body())
        resp = await api_client.post(BASE, json=_create_body())
        assert resp.status_code == 400


class TestList:
    async def test_list_empty(self, api_client):
        resp = await api_client.get(BASE)
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    async def test_list_after_create(self, api_client):
        await api_client.post(BASE, json=_create_body(name="a"))
        await api_client.post(BASE, json=_create_body(name="b", phase="pre_create", command="true"))
        resp = await api_client.get(BASE)
        names = {h["name"] for h in resp.json()["items"]}
        assert names == {"a", "b"}

    async def test_list_phase_filter(self, api_client):
        await api_client.post(BASE, json=_create_body(name="a"))
        await api_client.post(BASE, json=_create_body(name="b", phase="pre_create", command="true"))
        resp = await api_client.get(f"{BASE}?phase=pre_create")
        items = resp.json()["items"]
        assert [h["name"] for h in items] == ["b"]


class TestUpdate:
    async def test_update(self, api_client):
        created = (await api_client.post(BASE, json=_create_body())).json()
        resp = await api_client.put(
            f"{BASE}/{created['id']}",
            json={"command": "echo updated", "enabled": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["command"] == "echo updated"
        assert data["enabled"] is False

    async def test_update_unknown(self, api_client):
        resp = await api_client.put(f"{BASE}/9999", json={"command": "x"})
        assert resp.status_code == 404


class TestDelete:
    async def test_delete(self, api_client):
        created = (await api_client.post(BASE, json=_create_body())).json()
        resp = await api_client.delete(f"{BASE}/{created['id']}")
        assert resp.status_code == 204
        list_resp = await api_client.get(BASE)
        assert list_resp.json()["items"] == []

    async def test_delete_unknown(self, api_client):
        resp = await api_client.delete(f"{BASE}/9999")
        assert resp.status_code == 404


class TestReorder:
    async def test_reorder(self, api_client):
        a = (await api_client.post(BASE, json=_create_body(name="a"))).json()
        b = (await api_client.post(BASE, json=_create_body(name="b"))).json()
        c = (await api_client.post(BASE, json=_create_body(name="c"))).json()
        resp = await api_client.post(
            f"{BASE}/reorder",
            json={"phase": "post_create", "hook_ids": [c["id"], a["id"], b["id"]]},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert [h["name"] for h in items] == ["c", "a", "b"]

    async def test_reorder_mismatch(self, api_client):
        a = (await api_client.post(BASE, json=_create_body(name="a"))).json()
        resp = await api_client.post(
            f"{BASE}/reorder",
            json={"phase": "post_create", "hook_ids": [a["id"], 999]},
        )
        assert resp.status_code == 400


class TestRun:
    async def test_run_pre_local_success(self, api_client):
        created = (
            await api_client.post(
                BASE,
                json=_create_body(name="ok", phase="pre_create", command="/bin/true"),
            )
        ).json()
        resp = await api_client.post(f"{BASE}/{created['id']}/run")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    async def test_run_unknown(self, api_client):
        resp = await api_client.post(f"{BASE}/9999/run")
        assert resp.status_code == 404
