#!/usr/bin/env python3
"""End-to-end M17 policy prover demo script.

Drives the Z3 formal verification feature through the ShoreGuard HTTP
API.  Authenticates, picks the first gateway + sandbox, then runs all
four preset verification queries plus a custom exfiltration check.

Prereqs:
    * ShoreGuard running on ``http://127.0.0.1:8888`` with at least one
      gateway registered and one sandbox present.
    * ``SHOREGUARD_ADMIN_PASSWORD`` env var set.

Usage:
    uv run python scripts/m17_demo.py

Exit codes:
    0  All phases completed successfully.
    1  A phase failed.
    2  Missing prereq.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

SG = "http://127.0.0.1:8888"
ADMIN_EMAIL = "admin@localhost"


def banner(label: str) -> None:
    """Print a prominent phase banner."""
    print(f"\n\033[1;36m── {label} ──\033[0m")


def ok(msg: str) -> None:
    """Print a success line."""
    print(f"  \033[32m✔\033[0m {msg}")


def fail(msg: str) -> None:
    """Print a failure line and exit non-zero."""
    print(f"  \033[31m✘ {msg}\033[0m")
    sys.exit(1)


def require_env(name: str) -> str:
    """Return ``os.environ[name]`` or exit 2 with a helpful message."""
    val = os.environ.get(name)
    if not val:
        print(f"\033[31m✘ {name} is not set.\033[0m")
        sys.exit(2)
    return val


def main() -> None:
    """Run the M17 policy prover demo."""
    password = require_env("SHOREGUARD_ADMIN_PASSWORD")
    client = httpx.Client(base_url=SG, timeout=30.0)

    # ── Phase A: Authenticate ──────────────────────────────────────────
    banner("Phase A: Authenticate")
    r = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": password})
    if r.status_code != 200:
        fail(f"Login failed: {r.status_code} {r.text}")
    token = r.json().get("token") or r.cookies.get("sg_session")
    if token and "token" in r.json():
        client.headers["Authorization"] = f"Bearer {token}"
    ok("Logged in as admin")

    # ── Phase B: Pick gateway + sandbox ────────────────────────────────
    banner("Phase B: Pick gateway + sandbox")
    r = client.get("/api/gateways")
    if r.status_code != 200:
        fail(f"List gateways failed: {r.status_code}")
    gateways = r.json().get("gateways", [])
    if not gateways:
        fail("No gateways registered — register one first")
    gw_name = gateways[0]["name"]
    ok(f"Gateway: {gw_name}")

    r = client.get(f"/api/gateways/{gw_name}/sandboxes")
    if r.status_code != 200:
        fail(f"List sandboxes failed: {r.status_code}")
    sandboxes = r.json().get("sandboxes", [])
    if not sandboxes:
        fail("No sandboxes on gateway — create one first")
    sb_name = sandboxes[0]["name"]
    ok(f"Sandbox: {sb_name}")

    # ── Phase C: List presets ──────────────────────────────────────────
    banner("Phase C: List verification presets")
    r = client.get(f"/api/gateways/{gw_name}/sandboxes/{sb_name}/policy/verify/presets")
    if r.status_code != 200:
        fail(f"List presets failed: {r.status_code}")
    presets = r.json()
    for p in presets:
        ok(f"{p['query_id']}: {p['label']}")

    # ── Phase D: Run all preset queries ────────────────────────────────
    banner("Phase D: Run all preset queries")
    queries = []
    for p in presets:
        params = {}
        for pname, pdef in p.get("params", {}).items():
            if pname == "host_pattern":
                params[pname] = "*.evil.com"
            elif pname == "binary_path":
                params[pname] = "/usr/bin/curl"
            else:
                params[pname] = "test"
        queries.append({"query_id": p["query_id"], "params": params})

    r = client.post(
        f"/api/gateways/{gw_name}/sandboxes/{sb_name}/policy/verify",
        json={"queries": queries},
    )
    if r.status_code != 200:
        fail(f"Verify failed: {r.status_code} {r.text}")

    data = r.json()
    ok(f"Total time: {data['total_time_ms']:.1f} ms")
    for result in data["results"]:
        verdict = result["verdict"]
        color = (
            "\033[32m"
            if verdict == "SAFE"
            else "\033[31m"
            if verdict == "VULNERABLE"
            else "\033[33m"
        )
        print(f"  {color}{verdict}\033[0m  {result['query']}")
        if result.get("counterexample"):
            ce = result["counterexample"]
            print(f"    Counterexample: {json.dumps(ce, indent=None)}")

    # ── Phase E: Custom exfiltration check ─────────────────────────────
    banner("Phase E: Custom exfiltration check")
    r = client.post(
        f"/api/gateways/{gw_name}/sandboxes/{sb_name}/policy/verify",
        json={
            "queries": [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "*.malware.xyz"}},
            ]
        },
    )
    if r.status_code != 200:
        fail(f"Custom verify failed: {r.status_code}")
    result = r.json()["results"][0]
    verdict = result["verdict"]
    color = "\033[32m" if verdict == "SAFE" else "\033[31m"
    print(f"  {color}{verdict}\033[0m  {result['query']}")
    ok("Custom query completed")

    banner("Done")
    ok("M17 Policy Prover demo complete")


if __name__ == "__main__":
    main()
