#!/usr/bin/env python3
"""End-to-end in-k8s federation demo script.

k8s analog of ``scripts/m8_demo.py`` — proves that the Helm-deployed
ShoreGuard (``charts/shoreguard``) can federate two in-cluster OpenShell
gateways deployed via the internal test fixture
``tests/fixtures/charts/openshell-cluster`` (NOT a supported production
install path — see that chart's README), all via service-DNS mTLS,
inside a single kind cluster.

Differences from the host-process federation demo:

* Both gateways are pods in the same namespace. Their endpoint is the
  in-cluster Service DNS (``<release>-openshell-cluster.<ns>.svc.cluster.local:30051``),
  not ``127.0.0.1:<port>``.
* Gateways register with ``auth_mode=mtls`` / ``scheme=https`` — the
  client cert material is read from outer-k8s Secrets that the chart's
  bootstrap Job exports (``<release>-openshell-cluster-client-tls``).
* Every sandbox-exec step uses ShoreGuard's
  ``POST /api/gateways/{gw}/sandboxes/{sb}/exec`` LRO endpoint instead
  of shelling out to the ``openshell`` CLI — the host running this
  script does not need the openshell CLI installed and does not need
  reachable gateway HTTP endpoints, only a ``kubectl port-forward`` to
  ShoreGuard's Service.

Prereqs:

* A k8s cluster with:
    - ``tests/fixtures/charts/openshell-cluster`` installed as release
      ``cluster-dev`` with ``label.env=dev``
    - ``tests/fixtures/charts/openshell-cluster`` installed as release
      ``cluster-staging`` with ``label.env=staging``
    - Both bootstrap Jobs completed (``kubectl -n <ns> get jobs -l
      openshell.io/bootstrap=true``)
    - ``charts/shoreguard`` installed in the same namespace
    - ``kubectl port-forward svc/<sg-release>-shoreguard 8888:8888``
      running in the background
* Env vars:
    - ``SHOREGUARD_ADMIN_PASSWORD`` — whatever the ShoreGuard release
      was installed with (via ``admin.password`` or ``existingSecret``).
    - ``ANTHROPIC_API_KEY`` — real Anthropic key for routed inference.
    - ``NAMESPACE`` (optional, default ``shoreguard``) — namespace the
      releases live in.
    - ``SHOREGUARD_URL`` (optional, default ``http://127.0.0.1:8888``)
    - ``GW_DEV_RELEASE`` / ``GW_STAGING_RELEASE`` (optional, default
      ``cluster-dev`` / ``cluster-staging``).

Usage:

    uv run python scripts/m12_demo.py

Exit codes:
    0  All federation phases completed successfully.
    1  A phase failed; see the printed Phase X marker for the spot.
    2  Missing prereq (env var, Secret not found, etc.).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time

import httpx

SG = os.environ.get("SHOREGUARD_URL", "http://127.0.0.1:8888")
NAMESPACE = os.environ.get("NAMESPACE", "shoreguard")

GW_DEV = "cluster-dev"
GW_DEV_RELEASE = os.environ.get("GW_DEV_RELEASE", "cluster-dev")
GW_DEV_LABELS = {"env": "dev"}
DEMO_HOST_DEV = "httpbin.org"
DEMO_PATH_DEV = "/get"

GW_STAGING = "cluster-staging"
GW_STAGING_RELEASE = os.environ.get("GW_STAGING_RELEASE", "cluster-staging")
GW_STAGING_LABELS = {"env": "staging"}
DEMO_HOST_STAGING = "jsonplaceholder.typicode.com"
DEMO_PATH_STAGING = "/posts/1"

SB = "m12-base"
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


# ── k8s cert fetch ──────────────────────────────────────────────────────


def _fetch_cert_material(release: str) -> tuple[str, str, str]:
    """Read ca.crt / client.crt / client.key from the chart's client Secret.

    Values come back already base64-encoded (k8s Secret data is stored
    base64) so they can be handed straight to /api/gateway/register.
    Fails fast with a runbook-style hint if the Secret is absent.
    """
    secret = f"{release}-openshell-cluster-client-tls"
    try:
        raw = subprocess.run(
            ["kubectl", "-n", NAMESPACE, "get", "secret", secret, "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout
    except subprocess.CalledProcessError as e:
        fail(
            f"kubectl get secret {NAMESPACE}/{secret} failed: {e.stderr[:200]}\n"
            f"  → The tests/fixtures/charts/openshell-cluster bootstrap Job did not finish. "
            f"Check: kubectl -n {NAMESPACE} get jobs -l openshell.io/bootstrap=true"
        )
    data = json.loads(raw)["data"]
    try:
        return data["ca.crt"], data["client.crt"], data["client.key"]
    except KeyError as e:
        fail(f"Secret {secret} is missing key {e} — bootstrap did not export fully")
        raise  # unreachable (fail exits)


def _endpoint_for_release(release: str) -> str:
    """Return the in-cluster service DNS endpoint for a chart release."""
    return f"{release}-openshell-cluster.{NAMESPACE}.svc.cluster.local:30051"


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


# ── Phase B+C — register both gateways with mTLS + labels ──────────────


def register_gateway_mtls(
    client: httpx.Client,
    name: str,
    release: str,
    labels: dict,
) -> None:
    """Idempotent register: DELETE existing, then POST with mTLS creds."""
    endpoint = _endpoint_for_release(release)
    ca, crt, key = _fetch_cert_material(release)

    client.delete(f"/api/gateway/{name}")  # ignore status
    r = client.post(
        "/api/gateway/register",
        json={
            "name": name,
            "endpoint": endpoint,
            "scheme": "https",
            "auth_mode": "mtls",
            "description": f"in-k8s federation demo — {name}",
            "labels": labels,
            "ca_cert": ca,
            "client_cert": crt,
            "client_key": key,
        },
    )
    if r.status_code not in (200, 201):
        fail(f"{name} register failed: {r.status_code} {r.text[:200]}")
    body = r.json()
    if body.get("status") != "connected":
        fail(f"{name} registered but not connected: {body}")
    ok(f"{name} registered ({endpoint}) mTLS labels={labels} status=connected")


def phase_bc_register_both(client: httpx.Client) -> None:
    """Phase B+C — register both in-cluster gateways with mTLS + labels."""
    banner("Phase B+C — register both gateways with mTLS + labels")
    register_gateway_mtls(client, GW_DEV, GW_DEV_RELEASE, GW_DEV_LABELS)
    register_gateway_mtls(client, GW_STAGING, GW_STAGING_RELEASE, GW_STAGING_LABELS)


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


# ── LRO helper — wait for an exec op to finish and return the result ───


def _poll_operation(client: httpx.Client, op_id: str, timeout: float = 180) -> dict:
    """Poll ``GET /api/operations/<id>`` until terminal, return the op body."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/operations/{op_id}")
        if r.status_code == 200:
            op = r.json()
            if op.get("status") in ("succeeded", "success", "failed", "error"):
                return op
        time.sleep(2)
    fail(f"operation {op_id[:8]} timed out after {timeout}s")
    return {}  # unreachable


def _exec_in_sandbox(
    client: httpx.Client,
    gw: str,
    command: str,
    timeout_seconds: int = 60,
) -> dict:
    """Fire ``POST /api/gateways/{gw}/sandboxes/{sb}/exec`` and poll the LRO.

    Returns the op's ``result`` dict (keys: ``exit_code``, ``stdout``,
    ``stderr``) once terminal. Fails fast on non-202 responses.
    """
    r = client.post(
        f"/api/gateways/{gw}/sandboxes/{SB}/exec",
        json={"command": command, "timeout_seconds": timeout_seconds},
    )
    if r.status_code != 202:
        fail(f"{gw} exec create failed: {r.status_code} {r.text[:200]}")
    op_id = r.json()["operation_id"]
    op = _poll_operation(client, op_id, timeout=timeout_seconds + 60)
    if op.get("status") not in ("succeeded", "success"):
        fail(f"{gw} exec op failed: {op}")
    return op.get("result") or {}


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
    """Create the m12-base sandbox on ``gw`` via the LRO API and wait."""
    client.delete(f"/api/gateways/{gw}/sandboxes/{SB}")

    r = client.post(
        f"/api/gateways/{gw}/sandboxes",
        json={
            "name": SB,
            "providers": [PROVIDER],
            "description": f"in-k8s federation demo sandbox on {gw}",
        },
    )
    if r.status_code != 202:
        fail(f"{gw} sandbox create failed: {r.status_code} {r.text[:200]}")
    op_id = r.json()["operation_id"]

    deadline = time.time() + 240
    last_status = ""
    while time.time() < deadline:
        op = client.get(f"/api/operations/{op_id}").json()
        last_status = op.get("status", "")
        if last_status in ("succeeded", "success"):
            ok(f"{gw}: {SB} ready (op={op_id[:8]})")
            return
        if last_status in ("failed", "error"):
            fail(f"{gw} sandbox creation failed: {op}")
        time.sleep(2)
    fail(f"{gw} sandbox creation timed out (last status={last_status})")


def phase_e_per_gateway_setup(client: httpx.Client, anthropic_key: str) -> None:
    """Phase E — configure provider + inference + launch sandbox on both gateways."""
    banner("Phase E — provider + inference + sandbox on both gateways")
    for gw in (GW_DEV, GW_STAGING):
        configure_inference(client, gw, anthropic_key)
        launch_sandbox(client, gw)


# ── Phase F — routed inference call on each sandbox ────────────────────


def phase_f_routed_inference(client: httpx.Client) -> None:
    """Phase F — both sandboxes call Anthropic via their own routed proxy."""
    banner("Phase F — routed inference on both sandboxes (via exec API)")
    for gw in (GW_DEV, GW_STAGING):
        result = _exec_in_sandbox(
            client,
            gw,
            'claude -p "Reply with exactly the word PONG and nothing else."',
            timeout_seconds=90,
        )
        out = (result.get("stdout") or "").strip()
        if result.get("exit_code") != 0 or "PONG" not in out.upper():
            fail(
                f"{gw}: claude did not return PONG: "
                f"rc={result.get('exit_code')} out={out!r} "
                f"err={(result.get('stderr') or '')[:200]!r}"
            )
        ok(f"{gw}: claude returned {out!r}")


# ── Phase G — denial + approve + retry on each gateway ─────────────────


def provoke_denial(client: httpx.Client, gw: str, host: str) -> None:
    """Hit ``host`` from inside the sandbox to trigger an L7 denial."""
    result = _exec_in_sandbox(client, gw, f"curl -4 -sI https://{host}/", timeout_seconds=20)
    out = (result.get("stdout") or "") + (result.get("stderr") or "")
    if "403" not in out:
        warn(f"{gw}: expected 403 for {host}, got: {out[:200]!r}")
    else:
        ok(f"{gw}: L7 denial fired for {host}")


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


def retry_call(client: httpx.Client, gw: str, host: str, path: str) -> None:
    """Retry the previously denied call on ``gw``, expect HTTP 200."""
    result = _exec_in_sandbox(
        client,
        gw,
        (
            f"curl -4 -s -o /dev/null "
            f'-w "code=%{{http_code}} size=%{{size_download}}" '
            f"https://{host}{path}"
        ),
        timeout_seconds=30,
    )
    out = (result.get("stdout") or "").strip()
    if "code=200" not in out:
        fail(f"{gw}: retry did not return 200: {out!r}")
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
        provoke_denial(client, gw, host)
        chunk_id = find_pending_chunk(client, gw, host)
        approve_chunk(client, gw, chunk_id)
        retry_call(client, gw, host, path)


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


# ── main ────────────────────────────────────────────────────────────────


def main() -> int:
    """Run the full in-k8s federation demo end-to-end."""
    # Sanity-check the base64 encoding of the secrets once so we fail
    # loud and early rather than during gateway register.
    for rel in (GW_DEV_RELEASE, GW_STAGING_RELEASE):
        ca, crt, key = _fetch_cert_material(rel)
        for label, val in (("ca", ca), ("client.crt", crt), ("client.key", key)):
            try:
                base64.b64decode(val, validate=True)
            except Exception as e:
                fail(f"{rel}: {label} is not valid base64: {e}")

    password = require_env("SHOREGUARD_ADMIN_PASSWORD")
    anthropic_key = require_env("ANTHROPIC_API_KEY")

    print(
        f"\033[1mShoreGuard in-k8s federation demo\033[0m  "
        f"({SG} → ns={NAMESPACE} → {GW_DEV} + {GW_STAGING})"
    )

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
        "\n\033[1;32m✓ in-k8s federation demo complete — all phases passed.\033[0m\n"
        f"  Both in-cluster gateways ({GW_DEV} + {GW_STAGING}) and their sandboxes\n"
        f"  ({SB}) are left running. Inspect via port-forward in the browser.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
