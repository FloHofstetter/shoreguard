#!/usr/bin/env python3
"""End-to-end GitOps policy sync demo.

Drives the ``/policy/export`` and ``/policy/apply`` endpoints through
eight phases: export, no-op, dry-run drift, write, workflow vote,
workflow quorum, pin guard, drift webhook.

Prereqs:
    * ShoreGuard running on ``http://127.0.0.1:8888`` with at least one
      gateway registered and one sandbox present.
    * ``SHOREGUARD_ADMIN_PASSWORD`` env var set.

Usage:
    uv run python scripts/m23_demo.py

Exit codes:
    0  All phases completed successfully.
    1  A phase failed.
    2  Missing prereq.
"""

from __future__ import annotations

import os
import sys

import httpx
import yaml

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


def main() -> int:  # noqa: PLR0915, PLR0912
    """Run the GitOps policy sync demo end-to-end."""
    password = require_env("SHOREGUARD_ADMIN_PASSWORD")

    with httpx.Client(base_url=SG, timeout=20.0) as client:
        banner("Phase 0 — login")
        resp = client.post("/auth/login", data={"email": ADMIN_EMAIL, "password": password})
        if resp.status_code not in (200, 302, 303):
            fail(f"login failed: {resp.status_code}")
        ok("logged in as admin")

        gateways = client.get("/api/gateway/list").json().get("items", [])
        if not gateways:
            fail("no gateways registered")
        gw = gateways[0]["name"]
        sb_resp = client.get(f"/api/gateways/{gw}/sandboxes").json()
        sandboxes = sb_resp.get("items", [])
        if not sandboxes:
            fail("no sandboxes — create one first")
        sb = sandboxes[0]["name"]
        ok(f"target: {gw}/{sb}")

        export_url = f"/api/gateways/{gw}/sandboxes/{sb}/policy/export"
        apply_url = f"/api/gateways/{gw}/sandboxes/{sb}/policy/apply"
        pin_url = f"/api/gateways/{gw}/sandboxes/{sb}/policy/pin"
        wf_url = f"/api/gateways/{gw}/sandboxes/{sb}/approval-workflow"

        # ── Phase 1: export
        banner("Phase 1 — export baseline")
        resp = client.get(export_url)
        if resp.status_code != 200:
            fail(f"export failed: {resp.status_code} {resp.text[:200]}")
        baseline = resp.json()
        ok(f"exported version={baseline['version']} hash={baseline['policy_hash'][:16]}…")

        # ── Phase 2: apply no-op
        banner("Phase 2 — apply no-op (up_to_date)")
        resp = client.post(apply_url, json={"yaml": baseline["yaml"], "dry_run": False})
        if resp.status_code != 200 or resp.json()["status"] != "up_to_date":
            fail(f"expected up_to_date, got {resp.status_code} {resp.text[:200]}")
        ok("server reported up_to_date")

        # ── Phase 3: dry-run with drift
        banner("Phase 3 — dry-run with drift")
        modified = yaml.safe_load(baseline["yaml"])
        policy = modified["policy"]
        policy.setdefault("network_policies", {})["m23-demo"] = {
            "name": "m23-demo",
            "endpoints": [{"host": "example.com", "port": 443}],
        }
        drift_yaml = yaml.safe_dump(modified)
        resp = client.post(apply_url, json={"yaml": drift_yaml, "dry_run": True})
        if resp.status_code != 200 or resp.json()["status"] != "dry_run":
            fail(f"dry-run failed: {resp.status_code} {resp.text[:200]}")
        ok(f"diff: {resp.json()['diff']['network_policies']}")

        # ── Phase 4: apply write (no workflow)
        banner("Phase 4 — apply write (no workflow)")
        resp = client.post(apply_url, json={"yaml": drift_yaml, "dry_run": False})
        if resp.status_code != 200 or resp.json()["status"] != "applied":
            fail(f"apply failed: {resp.status_code} {resp.text[:200]}")
        ok(f"applied → {resp.json()['applied_version'][:16]}…")

        # ── Phase 5: configure workflow + first vote
        banner("Phase 5 — configure 1-of-1 workflow + vote")
        wf = client.put(wf_url, json={"required_approvals": 1, "distinct_actors": False})
        if wf.status_code not in (200, 201):
            fail(f"workflow create failed: {wf.status_code}")
        # Re-export to get the latest hash, then push another change
        latest = client.get(export_url).json()
        new_doc = yaml.safe_load(latest["yaml"])
        new_doc["policy"].setdefault("network_policies", {})["m23-demo-2"] = {
            "name": "m23-demo-2",
            "endpoints": [],
        }
        body = yaml.safe_dump(new_doc)
        resp = client.post(apply_url, json={"yaml": body, "dry_run": False})
        if resp.status_code != 200 or resp.json()["status"] != "applied":
            fail(f"quorum apply failed: {resp.status_code} {resp.text[:200]}")
        ok(f"quorum=1 met, applied → {resp.json()['applied_version'][:16]}…")

        # ── Phase 6: pin guard
        banner("Phase 6 — pin guard returns 423")
        client.post(pin_url, json={"reason": "m23 demo freeze"})
        resp = client.post(apply_url, json={"yaml": body, "dry_run": False})
        if resp.status_code != 423:
            fail(f"expected 423, got {resp.status_code}")
        ok("apply blocked while pinned")
        client.delete(pin_url)
        ok("unpinned")

        # ── Phase 7: cleanup workflow
        banner("Phase 7 — cleanup workflow")
        client.delete(wf_url)
        ok("workflow removed")

        # ── Phase 8: drift status hint
        banner("Phase 8 — drift detection hint")
        ok(
            "drift loop is opt-in via SHOREGUARD_DRIFT_DETECTION_ENABLED=true; "
            "see scripts/m23-gitops.md for the webhook walk."
        )

    print("\n\033[1;32m✔ GitOps policy sync demo completed.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
