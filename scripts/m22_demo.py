#!/usr/bin/env python3
"""End-to-end M22 boot hooks + discovery demo script.

Drives the new boot-hook CRUD/run endpoints and the gateway discovery
trigger. Authenticates, picks the first gateway + sandbox, creates a
post-create hook, runs it manually, lists it back, then exercises the
discovery endpoint with a stubbed (empty) result.

Prereqs:
    * ShoreGuard running on ``http://127.0.0.1:8888`` with at least one
      gateway registered and one sandbox present.
    * ``SHOREGUARD_ADMIN_PASSWORD`` env var set.

Usage:
    uv run python scripts/m22_demo.py

Exit codes:
    0  All phases completed successfully.
    1  A phase failed.
    2  Missing prereq.
"""

from __future__ import annotations

import os
import sys

import httpx

SG = "http://127.0.0.1:8888"
ADMIN_EMAIL = "admin@localhost"


def banner(label: str) -> None:
    """Print a phase banner."""
    print(f"\n\033[1;36m── {label} ──\033[0m")


def ok(msg: str) -> None:
    """Print a success line."""
    print(f"  \033[32m✔\033[0m {msg}")


def fail(msg: str) -> None:
    """Print a failure line and exit non-zero."""
    print(f"  \033[31m✘ {msg}\033[0m")
    sys.exit(1)


def require_env(name: str) -> str:
    """Return ``os.environ[name]`` or exit 2."""
    val = os.environ.get(name)
    if not val:
        print(f"\033[31m✘ {name} is not set.\033[0m")
        sys.exit(2)
    return val


def main() -> int:  # noqa: PLR0915
    """Run the M22 demo end-to-end."""
    password = require_env("SHOREGUARD_ADMIN_PASSWORD")

    with httpx.Client(base_url=SG, timeout=20.0) as client:
        # ── Phase 0: login
        banner("Phase 0 — login")
        resp = client.post(
            "/auth/login",
            data={"email": ADMIN_EMAIL, "password": password},
        )
        if resp.status_code not in (200, 302, 303):
            fail(f"login failed: {resp.status_code} {resp.text[:200]}")
        ok("logged in as admin")

        # ── Phase 1: discover a gateway + sandbox
        banner("Phase 1 — discover target sandbox")
        gateways = client.get("/api/gateway/list").json().get("items", [])
        if not gateways:
            fail("no gateways registered — register one and retry")
        gw = gateways[0]["name"]
        ok(f"using gateway {gw}")
        sb_resp = client.get(f"/api/gateways/{gw}/sandboxes").json()
        sandboxes = sb_resp.get("items", [])
        if not sandboxes:
            fail("no sandboxes found on gateway — create one and retry")
        sb = sandboxes[0]["name"]
        ok(f"using sandbox {sb}")

        base = f"/api/gateways/{gw}/sandboxes/{sb}/hooks"

        # ── Phase 2: create a post-create hook
        banner("Phase 2 — create boot hook")
        body = {
            "name": "m22-demo-hook",
            "phase": "post_create",
            "command": "echo m22-demo",
            "timeout_seconds": 5,
        }
        resp = client.post(base, json=body)
        if resp.status_code != 200:
            fail(f"create hook failed: {resp.status_code} {resp.text[:200]}")
        hook = resp.json()
        ok(f"hook created (id={hook['id']})")

        # ── Phase 3: list hooks
        banner("Phase 3 — list hooks")
        items = client.get(base).json()["items"]
        if not any(h["name"] == "m22-demo-hook" for h in items):
            fail("hook not present in list response")
        ok(f"hook list returned {len(items)} item(s)")

        # ── Phase 4: manually run the hook (post-create requires sandbox up)
        banner("Phase 4 — run hook manually")
        resp = client.post(f"{base}/{hook['id']}/run")
        if resp.status_code != 200:
            fail(f"run failed: {resp.status_code} {resp.text[:200]}")
        run_result = resp.json()
        ok(f"run status: {run_result['status']} ({run_result.get('summary', '')})")

        # ── Phase 5: cleanup hook
        banner("Phase 5 — cleanup hook")
        del_resp = client.delete(f"{base}/{hook['id']}")
        if del_resp.status_code != 204:
            fail(f"delete failed: {del_resp.status_code} {del_resp.text[:200]}")
        ok("hook deleted")

        # ── Phase 6: discovery trigger (will return empty unless DNS configured)
        banner("Phase 6 — discovery trigger")
        d_resp = client.post("/api/gateway/discover", json={})
        if d_resp.status_code not in (200, 503):
            fail(f"discovery failed: {d_resp.status_code} {d_resp.text[:200]}")
        if d_resp.status_code == 503:
            ok("discovery service not initialised (skipping)")
        else:
            res = d_resp.json()
            ok(
                f"discovery: {len(res['registered'])} registered, "
                f"{len(res['skipped'])} skipped, "
                f"{len(res['errors'])} errors"
            )

        # ── Phase 7: discovery status
        banner("Phase 7 — discovery status")
        s_resp = client.get("/api/gateway/discovery/status")
        if s_resp.status_code == 200:
            ok(f"status: {s_resp.json()}")
        else:
            ok(f"status endpoint returned {s_resp.status_code}")

    print("\n\033[1;32m✔ M22 demo completed.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
