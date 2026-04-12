#!/usr/bin/env python3
"""End-to-end M21 SBOM viewer demo script.

Drives the SBOM upload + query endpoints through the ShoreGuard HTTP API.
Authenticates, picks the first gateway + sandbox, uploads the bundled
CycloneDX fixture, then walks every read endpoint and finally deletes the
snapshot.

Prereqs:
    * ShoreGuard running on ``http://127.0.0.1:8888`` with at least one
      gateway registered and one sandbox present.
    * ``SHOREGUARD_ADMIN_PASSWORD`` env var set.

Usage:
    uv run python scripts/m21_demo.py

Exit codes:
    0  All phases completed successfully.
    1  A phase failed.
    2  Missing prereq.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

SG = "http://127.0.0.1:8888"
ADMIN_EMAIL = "admin@localhost"
FIXTURE = Path(__file__).parent / "fixtures" / "sample_cyclonedx.json"


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
    """Return ``os.environ[name]`` or exit 2 with a helpful message."""
    val = os.environ.get(name)
    if not val:
        print(f"\033[31m✘ {name} is not set.\033[0m")
        sys.exit(2)
    return val


def main() -> None:
    """Run the M21 SBOM viewer demo."""
    password = require_env("SHOREGUARD_ADMIN_PASSWORD")
    if not FIXTURE.exists():
        fail(f"Fixture not found: {FIXTURE}")
    raw_sbom = FIXTURE.read_text()

    client = httpx.Client(base_url=SG, timeout=30.0)

    # ── Phase A: Authenticate ──────────────────────────────────────────
    banner("Phase A: Authenticate")
    r = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": password})
    if r.status_code != 200:
        fail(f"Login failed: {r.status_code} {r.text}")
    body = r.json()
    if body.get("token"):
        client.headers["Authorization"] = f"Bearer {body['token']}"
    ok("Logged in as admin")

    # ── Phase B: Pick gateway + sandbox ────────────────────────────────
    banner("Phase B: Pick gateway + sandbox")
    r = client.get("/api/gateways")
    if r.status_code != 200:
        fail(f"List gateways failed: {r.status_code}")
    gateways = r.json().get("gateways", [])
    if not gateways:
        fail("No gateways registered")
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

    base = f"/api/gateways/{gw_name}/sandboxes/{sb_name}/sbom"

    # ── Phase C: Upload SBOM ───────────────────────────────────────────
    banner("Phase C: Upload CycloneDX SBOM")
    r = client.post(base, content=raw_sbom, headers={"Content-Type": "application/json"})
    if r.status_code != 201:
        fail(f"Upload failed: {r.status_code} {r.text}")
    snap = r.json()
    ok(
        f"Uploaded — components={snap['component_count']}, "
        f"vulns={snap['vulnerability_count']}, max={snap['max_severity']}"
    )

    # ── Phase D: Get snapshot metadata ─────────────────────────────────
    banner("Phase D: Get snapshot metadata")
    r = client.get(base)
    if r.status_code != 200:
        fail(f"Get snapshot failed: {r.status_code}")
    snap = r.json()
    ok(f"Spec {snap['bom_format']} {snap['spec_version']}, uploaded by {snap['uploaded_by']}")

    # ── Phase E: List components (search + severity filter) ───────────
    banner("Phase E: List components")
    r = client.get(f"{base}/components", params={"limit": 100})
    if r.status_code != 200:
        fail(f"List components failed: {r.status_code}")
    data = r.json()
    ok(f"All components: total={data['total']}")
    for c in data["items"]:
        sev = c["max_severity"] or "CLEAN"
        print(f"    - {c['name']}@{c['version']} ({c['type']}) [{sev}]")

    r = client.get(f"{base}/components", params={"search": "lodash"})
    matches = r.json()
    if matches["total"] != 1:
        fail(f"Search 'lodash' expected 1, got {matches['total']}")
    ok("Search 'lodash' → 1 hit")

    r = client.get(f"{base}/components", params={"severity": "CRITICAL"})
    crit = r.json()
    if crit["total"] != 1:
        fail(f"Severity CRITICAL expected 1, got {crit['total']}")
    ok(f"Severity CRITICAL → {crit['items'][0]['name']}")

    # ── Phase F: List vulnerabilities ──────────────────────────────────
    banner("Phase F: List vulnerabilities")
    r = client.get(f"{base}/vulnerabilities")
    if r.status_code != 200:
        fail(f"Vulns failed: {r.status_code}")
    vulns = r.json()["vulnerabilities"]
    for v in vulns:
        score = f" CVSS {v['cvss_score']}" if v.get("cvss_score") is not None else ""
        print(f"    - [{v['severity']}] {v['id']}{score}")
    if not vulns or vulns[0]["severity"] != "CRITICAL":
        fail("Expected first vuln to be CRITICAL")
    ok("Vulnerabilities sorted highest-severity first")

    # ── Phase G: Download raw payload ─────────────────────────────────
    banner("Phase G: Download raw CycloneDX payload")
    r = client.get(f"{base}/raw")
    if r.status_code != 200:
        fail(f"Raw download failed: {r.status_code}")
    if r.text != raw_sbom:
        fail("Raw download did not match uploaded payload byte-for-byte")
    ok(f"Raw payload OK ({len(r.content)} bytes, type={r.headers.get('content-type')})")

    # ── Phase H: Delete snapshot ──────────────────────────────────────
    banner("Phase H: Delete snapshot")
    r = client.delete(base)
    if r.status_code != 204:
        fail(f"Delete failed: {r.status_code}")
    ok("Snapshot deleted")
    r = client.get(base)
    if r.status_code != 404:
        fail(f"Expected 404 after delete, got {r.status_code}")
    ok("Confirmed 404 after delete")

    banner("Done")
    ok("M21 SBOM viewer demo complete")


if __name__ == "__main__":
    main()
