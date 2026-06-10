"""Integration test fixtures — real OpenShell gateway connection.

Gateway resolution order:
1. SHOREGUARD_TEST_ENDPOINT env var (e.g. "localhost:18080")
2. OPENSHELL_GATEWAY env var (connect via from_active_cluster)
3. Auto-start: foreground ``openshell-gateway`` daemon (v0.0.37+) with a
   tempfile SQLite state and plaintext gRPC. The process is torn down on
   session exit.
4. Skip all integration tests if none of the above work.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import grpc
import pytest

from shoreguard.client import ShoreGuardClient
from shoreguard.services.gateway import GatewayService
from shoreguard.services.policy import PolicyService
from shoreguard.services.sandbox import SandboxService

# Skip all integration tests when running under mutmut
_UNDER_MUTMUT = "mutants" in str(Path(__file__).resolve())

collect_ignore_glob = ["test_*.py"] if _UNDER_MUTMUT else []

logger = logging.getLogger("shoreguard.tests.integration")

_AUTO_GW_PORT = 18080
_AUTO_GW_HEALTH_PORT = 18081
_AUTO_GW_METRICS_PORT = 18082


def _wait_healthy(client: ShoreGuardClient, timeout: float = 120.0) -> None:
    """Poll health endpoint until the gateway is ready."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            resp = asyncio.run(client.health())
            if resp.get("status") in ("healthy", "ok"):
                return
        except Exception as e:
            last_err = e
        time.sleep(2)
    raise TimeoutError(f"Gateway not healthy within {timeout}s: {last_err}")


# Holds (popen, state_dir) for teardown when auto-start is used.
_AUTO_STATE: tuple[subprocess.Popen[bytes], str] | None = None


def _auto_start_gateway() -> str | None:
    """Spawn an ``openshell-gateway`` daemon (v0.0.37+) for the session.

    Returns ``localhost:<port>`` on success, or None if the binary is missing
    or the process exits before binding.
    """
    global _AUTO_STATE
    binary = shutil.which("openshell-gateway")
    if not binary:
        return None

    state_dir = tempfile.mkdtemp(prefix="shoreguard-it-gw-")
    db_url = f"sqlite:{state_dir}/openshell.db"
    grpc_endpoint = f"http://127.0.0.1:{_AUTO_GW_PORT}"
    cmd = [
        binary,
        "--bind-address",
        "127.0.0.1",
        "--port",
        str(_AUTO_GW_PORT),
        "--health-port",
        str(_AUTO_GW_HEALTH_PORT),
        "--metrics-port",
        str(_AUTO_GW_METRICS_PORT),
        "--disable-tls",
        "--disable-gateway-auth",
        "--db-url",
        db_url,
        "--grpc-endpoint",
        grpc_endpoint,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except Exception as e:  # pragma: no cover - environment specific
        logger.warning("Auto-start exception: %s", e)
        shutil.rmtree(state_dir, ignore_errors=True)
        return None

    # Give the daemon a moment to crash on bad flags.
    time.sleep(0.5)
    if proc.poll() is not None:
        stderr = (proc.stderr.read().decode() if proc.stderr else "").strip()
        logger.warning("openshell-gateway exited early: %s", stderr)
        shutil.rmtree(state_dir, ignore_errors=True)
        return None

    _AUTO_STATE = (proc, state_dir)
    return f"localhost:{_AUTO_GW_PORT}"


def _auto_destroy_gateway() -> None:
    """Stop the auto-started gateway daemon and remove its state directory."""
    global _AUTO_STATE
    if _AUTO_STATE is None:
        return
    proc, state_dir = _AUTO_STATE
    _AUTO_STATE = None
    try:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except Exception:
        pass
    shutil.rmtree(state_dir, ignore_errors=True)


# ── Auto-xfail UNIMPLEMENTED gRPC calls ───────────────────────────────────


def _is_unimplemented(excinfo) -> bool:
    """Check if an exception is a gRPC UNIMPLEMENTED error."""
    if excinfo is None:
        return False
    exc = excinfo.value if hasattr(excinfo, "value") else excinfo
    return isinstance(exc, grpc.RpcError) and exc.code() == grpc.StatusCode.UNIMPLEMENTED


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Convert UNIMPLEMENTED gRPC errors to skipped tests."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed and call.excinfo:
        if _is_unimplemented(call.excinfo):
            report.outcome = "skipped"
            report.wasxfail = "gRPC endpoint not implemented on this gateway version"


# ── Session-scoped fixtures ───────────────────────────────────────────────


@pytest.fixture(scope="session")
def gateway_endpoint():
    """Resolve a live gateway endpoint for integration tests."""
    # 1. Direct endpoint from env
    endpoint = os.environ.get("SHOREGUARD_TEST_ENDPOINT")
    if endpoint:
        yield endpoint
        return

    # 2. Named gateway from env
    gw_name = os.environ.get("OPENSHELL_GATEWAY")
    if gw_name:
        try:
            client = ShoreGuardClient.from_active_cluster(cluster=gw_name)

            async def _probe(c: ShoreGuardClient) -> None:
                await c.health()
                await c.close()

            asyncio.run(_probe(client))
            yield f"__cluster__:{gw_name}"
            return
        except Exception:
            pass

    # 3. Auto-start
    endpoint = _auto_start_gateway()
    if endpoint:
        yield endpoint
        _auto_destroy_gateway()
        return

    pytest.skip("No OpenShell gateway available for integration tests")


@pytest.fixture(scope="session")
def sg_client(gateway_endpoint: str):
    """Session-scoped ShoreGuardClient connected to the test gateway."""
    if gateway_endpoint.startswith("__cluster__:"):
        cluster_name = gateway_endpoint.split(":", 1)[1]
        client = ShoreGuardClient.from_active_cluster(cluster=cluster_name)
    else:
        client = ShoreGuardClient(gateway_endpoint)

    _wait_healthy(client)
    yield client
    asyncio.run(client.close())


# ── Function/module-scoped fixtures ───────────────────────────────────────


@pytest.fixture
def sandbox_factory(sg_client: ShoreGuardClient):
    """Factory that creates sandboxes and auto-cleans them after the test."""
    created: list[str] = []

    async def _make(*, name: str = "", image: str = "", **kwargs):
        sb = await sg_client.sandboxes.create(name=name, image=image, **kwargs)
        created.append(sb["name"])
        return sb

    yield _make

    async def _cleanup() -> None:
        for sb_name in reversed(created):
            try:
                await sg_client.sandboxes.delete(sb_name)
            except Exception:
                pass

    asyncio.run(_cleanup())


@pytest.fixture(scope="module")
def ready_sandbox(sg_client: ShoreGuardClient):
    """A sandbox that has reached 'ready' phase. Module-scoped for reuse."""
    sb = asyncio.run(sg_client.sandboxes.create(name=""))
    sb_name = sb["name"]
    try:
        sb = asyncio.run(sg_client.sandboxes.wait_ready(sb_name, timeout_seconds=120.0))
        yield sb
    finally:
        try:
            asyncio.run(sg_client.sandboxes.delete(sb_name))
        except Exception:
            pass


@pytest.fixture
def provider_factory(sg_client: ShoreGuardClient):
    """Factory that creates providers and auto-cleans them after the test."""
    created: list[str] = []

    async def _make(*, name: str, provider_type: str = "anthropic", **kwargs):
        prov = await sg_client.providers.create(name=name, provider_type=provider_type, **kwargs)
        created.append(prov["name"])
        return prov

    yield _make

    async def _cleanup() -> None:
        for prov_name in reversed(created):
            try:
                await sg_client.providers.delete(prov_name)
            except Exception:
                pass

    asyncio.run(_cleanup())


@pytest.fixture
def sandbox_service(sg_client: ShoreGuardClient):
    return SandboxService(sg_client)


@pytest.fixture
def policy_service(sg_client: ShoreGuardClient):
    return PolicyService(sg_client)


@pytest.fixture
def gateway_service(sg_client: ShoreGuardClient):
    from sqlalchemy.orm import sessionmaker as sa_sessionmaker

    from shoreguard.db import init_db
    from shoreguard.services.registry import GatewayRegistry

    engine = init_db("sqlite:///:memory:")
    session_factory = sa_sessionmaker(bind=engine)
    registry = GatewayRegistry(session_factory)
    svc = GatewayService(registry)
    svc.set_client(sg_client, name="integration-test")
    return svc
