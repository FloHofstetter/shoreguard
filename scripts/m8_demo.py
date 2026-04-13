#!/usr/bin/env python3
"""End-to-end multi-gateway federation demo script.

Drives the full federation flow (two gateways, labels, audit
attribution) through the ShoreGuard HTTP API + ``openshell`` CLI.
Operates on **two** real
OpenShell gateways with different labels and asserts that the
federation surfaces (label filter, audit attribution, gateway list)
behave correctly.

Idempotent: re-running deletes any leftover state on each gateway
before re-creating it.

Prereqs:
    * Two OpenShell gateways running locally:
        - ``cluster-dev`` on ``127.0.0.1:8089`` (label ``env=dev``)
        - ``cluster-staging`` on ``127.0.0.1:8189`` (label ``env=staging``)
      Started with ``openshell gateway start --name <name> --port <port>
      --plaintext --disable-gateway-auth`` for each cluster.

    * ShoreGuard running on ``http://127.0.0.1:8888`` with a fresh DB
      and ``SHOREGUARD_ADMIN_PASSWORD`` set so the bootstrap admin user
      gets seeded on first start.
    * Two env vars set on this script's process:
        - ``SHOREGUARD_ADMIN_PASSWORD`` — same value the running
          ShoreGuard was started with.
        - ``ANTHROPIC_API_KEY`` — a real Anthropic key so the routed
          inference calls on both sandboxes return real responses.

Usage:
    uv run python scripts/m8_demo.py

Exit codes:
    0  All federation phases completed successfully.
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

# Gateway #1 — dev cluster, a single-gateway vision-demo style workload.
GW_DEV = "cluster-dev"
GW_DEV_ENDPOINT = "127.0.0.1:8089"
GW_DEV_LABELS = {"env": "dev"}
DEMO_HOST_DEV = "httpbin.org"
DEMO_PATH_DEV = "/get"

# Gateway #2 — staging cluster, second federated peer.
GW_STAGING = "cluster-staging"
GW_STAGING_ENDPOINT = "127.0.0.1:8189"
GW_STAGING_LABELS = {"env": "staging"}
DEMO_HOST_STAGING = "jsonplaceholder.typicode.com"
DEMO_PATH_STAGING = "/posts/1"

# Per-gateway artefacts the script creates.
SB = "m8-base"
PROVIDER = "anthropic-demo"
MODEL = "claude-sonnet-4-5-20250929"

ADMIN_EMAIL = "admin@localhost"


# ── Output helpers ──────────────────────────────────────────────────────


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


# ── Phase A — login ─────────────────────────────────────────────────────


def phase_a_login(client: httpx.Client, password: str) -> None:
    """Phase A — log in as the bootstrap admin."""
    banner("Phase A — login")
    r = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": password},
    )
    if r.status_code != 200:
        fail(f"login failed: {r.status_code} {r.text[:200]}")
    ok(f"logged in as {ADMIN_EMAIL}")


# ── Phase B+C — register both gateways with labels ──────────────────────


def register_gateway(
    client: httpx.Client,
    name: str,
    endpoint: str,
    labels: dict,
) -> None:
    """Idempotent register: DELETE existing, then POST with labels.

    Works around the local-mode auto-register-with-mtls bug — the
    auto-registered record carries cert material and the wrong
    auth_mode, which leaves the gateway permanently unreachable
    until it's replaced with an explicit ``auth_mode=insecure``
    registration.
    """
    client.delete(f"/api/gateway/{name}")  # ignore status — may not exist
    r = client.post(
        "/api/gateway/register",
        json={
            "name": name,
            "endpoint": endpoint,
            "scheme": "http",
            "auth_mode": "insecure",
            "description": f"federation demo — {name}",
            "labels": labels,
        },
    )
    if r.status_code not in (200, 201):
        fail(f"{name} register failed: {r.status_code} {r.text[:200]}")
    body = r.json()
    if body.get("status") != "connected":
        fail(f"{name} registered but not connected: {body}")
    ok(f"{name} registered ({endpoint}) labels={labels} status=connected")


def phase_bc_register_both(client: httpx.Client) -> None:
    """Phase B+C — clean register both gateways with their labels."""
    banner("Phase B+C — register both gateways with labels")
    register_gateway(client, GW_DEV, GW_DEV_ENDPOINT, GW_DEV_LABELS)
    register_gateway(client, GW_STAGING, GW_STAGING_ENDPOINT, GW_STAGING_LABELS)


# ── Phase D — label filter assertions ───────────────────────────────────


def phase_d_label_filter(client: httpx.Client) -> None:
    """Phase D — assert ?label= filter narrows the list correctly."""
    banner("Phase D — federation assertion: label filter")

    r = client.get("/api/gateway/list")
    total = r.json().get("total")
    items = [g["name"] for g in r.json().get("items", [])]
    if total != 2 or set(items) != {GW_DEV, GW_STAGING}:
        fail(f"unfiltered list expected both gateways, got total={total} items={items}")
    ok(f"unfiltered list returns both: {sorted(items)}")

    r = client.get("/api/gateway/list?label=env:dev")
    items = [g["name"] for g in r.json().get("items", [])]
    if items != [GW_DEV]:
        fail(f"?label=env:dev expected [{GW_DEV}], got {items}")
    ok(f"?label=env:dev → {items}")

    r = client.get("/api/gateway/list?label=env:staging")
    items = [g["name"] for g in r.json().get("items", [])]
    if items != [GW_STAGING]:
        fail(f"?label=env:staging expected [{GW_STAGING}], got {items}")
    ok(f"?label=env:staging → {items}")


# ── Phase E — provider + inference + sandbox per gateway ───────────────


def configure_inference(client: httpx.Client, gw: str, anthropic_key: str) -> None:
    """Create the anthropic provider and wire the inference route on ``gw``."""
    client.delete(f"/api/gateways/{gw}/providers/{PROVIDER}")
    r = client.post(
        f"/api/gateways/{gw}/providers",
        json={
            "name": PROVIDER,
            "type": "anthropic",
            "credentials": {"ANTHROPIC_API_KEY": anthropic_key},
        },
    )
    if r.status_code not in (200, 201):
        fail(f"{gw} provider create failed: {r.status_code} {r.text[:200]}")

    inference_body = {
        "provider_name": PROVIDER,
        "model_id": MODEL,
        "verify": False,
        "timeout_secs": 30,
    }
    last: httpx.Response | None = None
    for _ in range(5):
        last = client.put(f"/api/gateways/{gw}/inference", json=inference_body)
        if last.status_code == 200:
            break
        time.sleep(1)
    assert last is not None
    if last.status_code != 200:
        fail(f"{gw} set_inference failed: {last.status_code} {last.text[:200]}")
    ok(f"{gw}: provider {PROVIDER} + inference route wired")


def launch_sandbox(client: httpx.Client, gw: str) -> None:
    """Create the m8-base sandbox on ``gw`` via the LRO API and wait."""
    client.delete(f"/api/gateways/{gw}/sandboxes/{SB}")

    r = client.post(
        f"/api/gateways/{gw}/sandboxes",
        json={
            "name": SB,
            "providers": [PROVIDER],
            "description": f"federation demo sandbox on {gw}",
        },
    )
    if r.status_code != 202:
        fail(f"{gw} sandbox create failed: {r.status_code} {r.text[:200]}")
    op_id = r.json()["operation_id"]

    deadline = time.time() + 180
    last_status = ""
    while time.time() < deadline:
        op = client.get(f"/api/operations/{op_id}").json()
        last_status = op.get("status", "")
        if last_status in ("succeeded", "success"):
            ok(f"{gw}: {SB} ready (op={op_id[:8]})")
            wait_for_exec_ready(gw)
            return
        if last_status in ("failed", "error"):
            fail(f"{gw} sandbox creation failed: {op}")
        time.sleep(2)
    fail(f"{gw} sandbox creation timed out (last status={last_status})")


def wait_for_exec_ready(gw: str) -> None:
    """Poll ``openshell sandbox exec ... -- true`` until the SSH transport is up.

    Same race as in ``m7_demo.py``: the LRO flips to ``succeeded`` once
    the cluster reports ``phase=ready``, but the in-sandbox SSH
    endpoint can need up to ~60s before it accepts connections.
    """
    deadline = time.time() + 90
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        proc = subprocess.run(
            [
                "openshell",
                "--gateway-endpoint",
                f"http://{_endpoint_for(gw)}",
                "sandbox",
                "exec",
                "--name",
                SB,
                "--timeout",
                "5",
                "--",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            ok(f"{gw}: exec transport ready (after {attempt} probe(s))")
            return
        time.sleep(2)
    fail(f"{gw}: exec transport never became reachable within 90s ({attempt} probes)")


def phase_e_per_gateway_setup(client: httpx.Client, anthropic_key: str) -> None:
    """Phase E — configure provider + inference + launch sandbox on both gateways."""
    banner("Phase E — provider + inference + sandbox on both gateways")
    for gw in (GW_DEV, GW_STAGING):
        configure_inference(client, gw, anthropic_key)
        launch_sandbox(client, gw)


# ── Phase F — routed inference call on each sandbox ────────────────────


def routed_inference_call(gw: str) -> None:
    """Run ``claude -p PONG`` inside the sandbox on ``gw``."""
    proc = subprocess.run(
        [
            "openshell",
            "--gateway-endpoint",
            f"http://{_endpoint_for(gw)}",
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
        fail(f"{gw}: claude did not return PONG: rc={proc.returncode} out={out!r} err={err!r}")
    ok(f"{gw}: claude returned {out!r}")


def phase_f_routed_inference(_client: httpx.Client) -> None:
    """Phase F — both sandboxes call Anthropic via their own routed proxy."""
    banner("Phase F — routed inference on both sandboxes")
    for gw in (GW_DEV, GW_STAGING):
        routed_inference_call(gw)


# ── Phase G — denial + approve + retry on each gateway ─────────────────


def provoke_denial(gw: str, host: str) -> None:
    """Hit ``host`` from inside the sandbox to trigger an L7 denial."""
    proc = subprocess.run(
        [
            "openshell",
            "--gateway-endpoint",
            f"http://{_endpoint_for(gw)}",
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
            f"https://{host}/",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = proc.stdout or ""
    if "403" not in out:
        warn(f"{gw}: expected 403 for {host}, got: {out[:200]!r}")
    else:
        ok(f"{gw}: L7 denial fired (HTTP/1.1 403 from proxy CONNECT for {host})")


def find_pending_chunk(client: httpx.Client, gw: str, host_substr: str) -> str:
    """Return the pending chunk ID for the given host on ``gw``."""
    deadline = time.time() + 30
    while time.time() < deadline:
        r = client.get(f"/api/gateways/{gw}/sandboxes/{SB}/approvals")
        if r.status_code == 200:
            for chunk in r.json().get("chunks", []):
                if chunk.get("status") != "pending":
                    continue
                rule = chunk.get("proposed_rule", {})
                for ep in rule.get("endpoints", []):
                    if host_substr in (ep.get("host") or ""):
                        return chunk["id"]
        time.sleep(1)
    fail(f"{gw}: no pending chunk for host containing {host_substr!r} appeared within 30s")
    return ""  # unreachable


def approve_chunk(client: httpx.Client, gw: str, chunk_id: str) -> int:
    """Approve a chunk on ``gw`` and return the resulting policy version."""
    r = client.post(
        f"/api/gateways/{gw}/sandboxes/{SB}/approvals/{chunk_id}/approve",
        params={"wait_loaded": "true"},
    )
    if r.status_code != 200:
        fail(f"{gw}: approve failed: {r.status_code} {r.text[:200]}")
    version = int(r.json().get("policy_version", 0))
    ok(f"{gw}: chunk {chunk_id[:8]} approved → policy_version={version} (waited)")


def retry_call(gw: str, host: str, path: str) -> None:
    """Retry the previously denied call on ``gw``, expect HTTP 200."""
    proc = subprocess.run(
        [
            "openshell",
            "--gateway-endpoint",
            f"http://{_endpoint_for(gw)}",
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
            f"https://{host}{path}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = (proc.stdout or "").strip()
    if "code=200" not in out:
        fail(f"{gw}: retry did not return 200: {out!r} stderr={proc.stderr[:200]!r}")
    ok(f"{gw}: retry succeeded → {out}")


def phase_g_denial_approve_retry(client: httpx.Client) -> None:
    """Phase G — denial + approve + retry on EACH gateway with a different host."""
    banner(
        f"Phase G — L7 denial + approve + retry "
        f"({GW_DEV}: {DEMO_HOST_DEV} | {GW_STAGING}: {DEMO_HOST_STAGING})"
    )

    for gw, host, path in (
        (GW_DEV, DEMO_HOST_DEV, DEMO_PATH_DEV),
        (GW_STAGING, DEMO_HOST_STAGING, DEMO_PATH_STAGING),
    ):
        provoke_denial(gw, host)
        chunk_id = find_pending_chunk(client, gw, host)
        approve_chunk(client, gw, chunk_id)
        retry_call(gw, host, path)


# ── Phase H — federation assertion: per-gateway audit attribution ──────


def phase_h_audit_attribution(client: httpx.Client) -> None:
    """Phase H — assert audit ?gateway= filter only returns rows for that gateway."""
    banner("Phase H — federation assertion: audit attribution per gateway")

    for gw in (GW_DEV, GW_STAGING):
        r = client.get(f"/api/audit?gateway={gw}&limit=200")
        if r.status_code != 200:
            fail(f"audit query for {gw} failed: {r.status_code}")
        entries = r.json().get("entries", [])
        wrong = [e for e in entries if e.get("gateway") != gw]
        if wrong:
            fail(
                f"{gw}: audit returned {len(wrong)} rows tagged with the wrong gateway: {wrong[:2]}"
            )
        ok(f"{gw}: {len(entries)} audit entries, all correctly attributed to {gw}")


# ── Phase I — federation assertion: unfiltered audit shows both ────────


def phase_i_audit_unfiltered(client: httpx.Client) -> None:
    """Phase I — unfiltered audit log shows entries from BOTH gateways."""
    banner("Phase I — federation assertion: unfiltered audit shows both gateways")

    r = client.get("/api/audit?limit=200")
    entries = r.json().get("entries", [])
    by_gw: dict[str, int] = {}
    for e in entries:
        g = e.get("gateway") or "<global>"
        by_gw[g] = by_gw.get(g, 0) + 1

    print(f"  audit row distribution across {len(entries)} rows:")
    for g, n in sorted(by_gw.items()):
        print(f"    {g:20s} {n}")

    if by_gw.get(GW_DEV, 0) == 0:
        fail(f"no audit rows for {GW_DEV}")
    if by_gw.get(GW_STAGING, 0) == 0:
        fail(f"no audit rows for {GW_STAGING}")
    ok(
        f"both gateways present in unfiltered audit "
        f"({by_gw[GW_DEV]} dev, {by_gw[GW_STAGING]} staging)"
    )


# ── Phase J — federation assertion: gateway list with labels ───────────


def phase_j_gateway_list(client: httpx.Client) -> None:
    """Phase J — gateway list returns both with labels and status=connected."""
    banner("Phase J — federation assertion: /api/gateway/list")

    r = client.get("/api/gateway/list")
    items = {g["name"]: g for g in r.json().get("items", [])}
    for gw, expected_labels in (
        (GW_DEV, GW_DEV_LABELS),
        (GW_STAGING, GW_STAGING_LABELS),
    ):
        g = items.get(gw)
        if not g:
            fail(f"{gw} missing from /api/gateway/list")
        if g.get("labels") != expected_labels:
            fail(f"{gw} labels mismatch: got {g.get('labels')} expected {expected_labels}")
        if g.get("status") != "connected":
            fail(f"{gw} not connected: {g.get('status')}")
        ok(f"{gw}: status=connected labels={g.get('labels')}")


# ── helpers ─────────────────────────────────────────────────────────────


def _endpoint_for(gw: str) -> str:
    return GW_DEV_ENDPOINT if gw == GW_DEV else GW_STAGING_ENDPOINT


# ── main ────────────────────────────────────────────────────────────────


def main() -> int:
    """Run the full federation demo end-to-end."""
    password = require_env("SHOREGUARD_ADMIN_PASSWORD")
    anthropic_key = require_env("ANTHROPIC_API_KEY")

    print(f"\033[1mShoreGuard federation demo\033[0m  ({SG} → {GW_DEV} + {GW_STAGING})")

    with httpx.Client(base_url=SG, timeout=30.0) as client:
        phase_a_login(client, password)
        phase_bc_register_both(client)
        phase_d_label_filter(client)
        phase_e_per_gateway_setup(client, anthropic_key)
        phase_f_routed_inference(client)
        phase_g_denial_approve_retry(client)
        phase_h_audit_attribution(client)
        phase_i_audit_unfiltered(client)
        phase_j_gateway_list(client)

    print(
        "\n\033[1;32m✓ federation demo complete — all phases passed.\033[0m\n"
        f"  Both gateways ({GW_DEV} + {GW_STAGING}) and their sandboxes ({SB}) are\n"
        f"  left running for manual UX inspection in the browser.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
