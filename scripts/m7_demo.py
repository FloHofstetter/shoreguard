#!/usr/bin/env python3
"""End-to-end M7 vision demo script.

Drives the full M7 flow through the ShoreGuard HTTP API + ``openshell``
CLI for the in-sandbox commands. Mirrors ``scripts/m7-demo.md`` step
by step. Designed to be idempotent — re-running it deletes the
previous demo sandbox and gateway-registration before starting.

Prereqs:
    * OpenShell ``nemoclaw`` gateway already running locally on
      ``127.0.0.1:8089`` (``openshell gateway start --name nemoclaw
      --port 8089 --plaintext --disable-gateway-auth``).
    * ShoreGuard running on ``http://127.0.0.1:8888`` with a fresh DB
      and ``SHOREGUARD_ADMIN_PASSWORD`` set so the bootstrap admin user
      gets seeded on first start.
    * Two env vars set on this script's process:
        - ``SHOREGUARD_ADMIN_PASSWORD`` — same value the running
          ShoreGuard was started with.
        - ``ANTHROPIC_API_KEY`` — a real Anthropic key so the routed
          inference call returns a real response.

Usage:
    uv run python scripts/m7_demo.py

Exit codes:
    0  All 8 phases completed successfully.
    1  A phase failed; see the printed Phase X marker for the spot.
    2  Missing prereq (env var, gateway not reachable, etc.).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx

SG = "http://127.0.0.1:8888"
GW = "nemoclaw"
GW_ENDPOINT = "127.0.0.1:8089"
SB = "m7-demo-sb"
PROVIDER = "anthropic-demo"
MODEL = "claude-sonnet-4-5-20250929"
DEMO_HOST = "jsonplaceholder.typicode.com"
DEMO_PATH = "/posts/1"

ADMIN_EMAIL = "admin@localhost"


def banner(label: str) -> None:
    """Print a prominent phase banner."""
    print(f"\n\033[1;36m── {label} ──\033[0m")


def ok(msg: str) -> None:
    """Print a success line."""
    print(f"  \033[32m✔\033[0m {msg}")


def warn(msg: str) -> None:
    """Print a warning line."""
    print(f"  \033[33m!\033[0m {msg}")


def fail(msg: str) -> None:
    """Print a failure line and exit non-zero."""
    print(f"  \033[31m✘ {msg}\033[0m")
    sys.exit(1)


def require_env(name: str) -> str:
    """Return ``os.environ[name]`` or exit 2 with a helpful message."""
    val = os.environ.get(name)
    if not val:
        print(f"\033[31m✘ {name} is not set in this shell.\033[0m")
        print(f"  export {name}=... and re-run.")
        sys.exit(2)
    return val


def login(client: httpx.Client, password: str) -> None:
    """Phase A.1 — log in as the bootstrap admin."""
    banner("Phase A.1 — login")
    r = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": password},
    )
    if r.status_code != 200:
        fail(f"login failed: {r.status_code} {r.text[:200]}")
    ok(f"logged in as {ADMIN_EMAIL}")


def register_gateway(client: httpx.Client) -> None:
    """Phase A.2 — DELETE any existing nemoclaw, then register fresh.

    Works around the local-mode auto-register-with-mtls bug: in
    SHOREGUARD_LOCAL_MODE=true ShoreGuard auto-registers nemoclaw with
    cert material on startup, but the local gateway is plaintext, so
    the connection sits at ``last_status=unreachable`` until we replace
    it with an explicit ``auth_mode=insecure`` registration.
    """
    banner("Phase A.2 — register gateway")
    client.delete(f"/api/gateway/{GW}")  # ignore status — may not exist
    r = client.post(
        "/api/gateway/register",
        json={
            "name": GW,
            "endpoint": GW_ENDPOINT,
            "scheme": "http",
            "auth_mode": "insecure",
            "description": "M7 demo gateway (scripted)",
        },
    )
    if r.status_code not in (200, 201):
        fail(f"register failed: {r.status_code} {r.text[:200]}")
    body = r.json()
    if body.get("status") != "connected":
        fail(f"gateway registered but not connected: {body}")
    ok(f"{GW} registered at {GW_ENDPOINT}, status=connected")


def configure_inference(client: httpx.Client, anthropic_key: str) -> None:
    """Phase B — create the anthropic provider and wire it as the inference route.

    Note the ambiguity surfaced in the first dry-run: ``set_inference``
    takes the *provider record name* (here ``anthropic-demo``), not
    the inference provider *type* (``anthropic``).
    """
    banner("Phase B — inference provider")

    # Replace any leftover provider record from a previous run.
    client.delete(f"/api/gateways/{GW}/providers/{PROVIDER}")
    r = client.post(
        f"/api/gateways/{GW}/providers",
        json={
            "name": PROVIDER,
            "type": "anthropic",
            "credentials": {"ANTHROPIC_API_KEY": anthropic_key},
        },
    )
    if r.status_code not in (200, 201):
        fail(f"provider create failed: {r.status_code} {r.text[:200]}")
    ok(f"provider {PROVIDER} created")

    # set_inference can transiently 409 with FAILED_PRECONDITION right
    # after a provider re-create — the cluster needs a beat to see the
    # new provider record. Retry briefly.
    inference_body = {
        "provider_name": PROVIDER,
        "model_id": MODEL,
        "verify": False,
        "timeout_secs": 30,
    }
    last: httpx.Response | None = None
    for _ in range(5):
        last = client.put(f"/api/gateways/{GW}/inference", json=inference_body)
        if last.status_code == 200:
            break
        time.sleep(1)
    assert last is not None
    if last.status_code != 200:
        fail(f"set_inference failed: {last.status_code} {last.text[:200]}")
    ok(f"inference wired: route_name={last.json().get('route_name')}")


def launch_sandbox(client: httpx.Client) -> None:
    """Phase C — create the m7-demo-sb sandbox via the LRO API and wait."""
    banner("Phase C — launch sandbox")

    # Best-effort cleanup of any stale sandbox from a previous run.
    client.delete(f"/api/gateways/{GW}/sandboxes/{SB}")

    r = client.post(
        f"/api/gateways/{GW}/sandboxes",
        json={
            "name": SB,
            "providers": [PROVIDER],
            "description": "M7 demo sandbox (scripted)",
        },
    )
    if r.status_code != 202:
        fail(f"sandbox create failed: {r.status_code} {r.text[:200]}")
    op_id = r.json()["operation_id"]

    deadline = time.time() + 180  # base image is cached locally; 180s is plenty
    last_status = ""
    while time.time() < deadline:
        op = client.get(f"/api/operations/{op_id}").json()
        last_status = op.get("status", "")
        if last_status in ("succeeded", "success"):
            ok(f"{SB} ready (op={op_id[:8]})")
            wait_for_exec_ready()
            return
        if last_status in ("failed", "error"):
            fail(f"sandbox creation failed: {op}")
        time.sleep(2)
    fail(f"sandbox creation timed out (last status={last_status})")


def wait_for_exec_ready() -> None:
    """Wait for the in-sandbox exec/ssh transport to be reachable.

    Even after the LRO operation flips to ``succeeded``, the
    in-sandbox SSH endpoint that ``openshell sandbox exec`` rides on
    can need up to ~60s before it accepts connections on a truly
    fresh sandbox. Empirically the failure mode goes through three
    stages: ``ssh transport: Connection reset by peer`` for the first
    ~30s, then ``phase: Provisioning`` (a brief restart bounce), then
    finally a clean exec.

    Polls a cheap ``openshell sandbox exec ... -- true`` every 2s for
    up to 90s. Fails loudly if the sandbox never becomes execable.
    """
    deadline = time.time() + 90
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        proc = subprocess.run(
            ["openshell", "sandbox", "exec", "--name", SB, "--timeout", "5", "--", "true"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            ok(f"exec transport ready (after {attempt} probe(s))")
            return
        time.sleep(2)
    fail(f"exec transport never became reachable within 90s ({attempt} probes)")


def routed_inference_call() -> None:
    """Phase D — claude inside the sandbox calls Anthropic via the routed proxy."""
    banner("Phase D — routed inference (the previously unproven step)")
    proc = subprocess.run(
        [
            "openshell",
            "sandbox",
            "exec",
            "--name",
            SB,
            "--timeout",
            "60",
            "--",
            "claude",
            "-p",
            "Reply with exactly the word PONG and nothing else.",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or "PONG" not in out.upper():
        err = (proc.stderr or "")[:200]
        fail(f"claude call did not return PONG: rc={proc.returncode} out={out!r} err={err!r}")
    ok(f"claude returned: {out!r}")


def provoke_denial() -> None:
    """Phase E — unallowlisted curl produces a 403 + draft chunk upstream."""
    banner(f"Phase E — provoke L7 denial on {DEMO_HOST}")
    proc = subprocess.run(
        [
            "openshell",
            "sandbox",
            "exec",
            "--name",
            SB,
            "--timeout",
            "15",
            "--",
            "curl",
            "-4",
            "-sI",
            f"https://{DEMO_HOST}/",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = proc.stdout or ""
    if "403" not in out:
        warn(f"expected a 403, got: {out[:200]!r}")
    else:
        ok(f"L7 denial fired (HTTP/1.1 403 from proxy CONNECT for {DEMO_HOST})")


def find_pending_chunk(client: httpx.Client, host_substr: str) -> str:
    """Return the chunk ID for the first pending draft chunk matching ``host_substr``."""
    deadline = time.time() + 30
    while time.time() < deadline:
        r = client.get(f"/api/gateways/{GW}/sandboxes/{SB}/approvals")
        if r.status_code == 200:
            for chunk in r.json().get("chunks", []):
                if chunk.get("status") != "pending":
                    continue
                rule = chunk.get("proposed_rule", {})
                for ep in rule.get("endpoints", []):
                    if host_substr in (ep.get("host") or ""):
                        return chunk["id"]
        time.sleep(1)
    fail(f"no pending chunk for host containing {host_substr!r} appeared within 30s")
    return ""  # unreachable


def approve_chunk(client: httpx.Client, chunk_id: str) -> int:
    """Approve a chunk and return the resulting policy version."""
    banner(f"Phase F — approve chunk {chunk_id[:8]}")
    r = client.post(
        f"/api/gateways/{GW}/sandboxes/{SB}/approvals/{chunk_id}/approve",
    )
    if r.status_code != 200:
        fail(f"approve failed: {r.status_code} {r.text[:200]}")
    version = int(r.json().get("policy_version", 0))
    ok(f"chunk approved → policy_version={version}")
    return version


def wait_policy_loaded(client: httpx.Client, target_version: int) -> None:
    """Poll the policy endpoint until the target version is reported as ``loaded``.

    This is the fix for the race that bit the first dry-run: ``/approve``
    returns synchronously with a new version, but the proxy loads it
    asynchronously. Retrying before the load completes hits the proxy
    under the old policy and produces a fresh 403.
    """
    deadline = time.time() + 30
    while time.time() < deadline:
        r = client.get(f"/api/gateways/{GW}/sandboxes/{SB}/policy")
        if r.status_code == 200:
            d = r.json()
            if (
                d.get("active_version") == target_version
                and d.get("revision", {}).get("status") == "loaded"
            ):
                ok(f"policy v{target_version} loaded on the proxy")
                return
        time.sleep(1)
    fail(f"policy v{target_version} did not reach 'loaded' state within 30s")


def show_audit_sequence(client: httpx.Client) -> None:
    """Phase G — print the per-gateway audit sequence."""
    banner(f"Phase G — audit sequence for gateway={GW}")
    r = client.get(f"/api/audit?gateway={GW}&limit=50")
    if r.status_code != 200:
        fail(f"audit query failed: {r.status_code}")
    entries = sorted(r.json().get("entries", []), key=lambda e: e["timestamp"])
    print(f"  {len(entries)} entries:")
    for e in entries:
        ts = e["timestamp"][11:19]
        print(f"    {ts}  {e['action']:24s}  {e['resource_type']:10s}  {e['resource_id']}")
    ok("audit sequence captured")


def retry_call() -> None:
    """Phase H — retry the previously denied call, expect HTTP 200."""
    banner("Phase H — retry the call (now allowed)")
    proc = subprocess.run(
        [
            "openshell",
            "sandbox",
            "exec",
            "--name",
            SB,
            "--timeout",
            "15",
            "--",
            "curl",
            "-4",
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "code=%{http_code} size=%{size_download}",
            f"https://{DEMO_HOST}{DEMO_PATH}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = (proc.stdout or "").strip()
    if "code=200" not in out:
        fail(f"retry did not return 200: {out!r} stderr={proc.stderr[:200]!r}")
    ok(f"retry succeeded: {out}")


def main() -> int:
    """Run the full M7 demo end-to-end."""
    password = require_env("SHOREGUARD_ADMIN_PASSWORD")
    anthropic_key = require_env("ANTHROPIC_API_KEY")

    print(f"\033[1mShoreGuard M7 vision demo\033[0m  ({SG} → {GW} → {SB})")

    with httpx.Client(base_url=SG, timeout=30.0) as client:
        login(client, password)
        register_gateway(client)
        configure_inference(client, anthropic_key)
        launch_sandbox(client)
        routed_inference_call()
        provoke_denial()
        chunk_id = find_pending_chunk(client, DEMO_HOST)
        version = approve_chunk(client, chunk_id)
        wait_policy_loaded(client, version)
        show_audit_sequence(client)
        retry_call()

    print("\n\033[1;32m✓ M7 demo complete — all 8 phases passed.\033[0m\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
