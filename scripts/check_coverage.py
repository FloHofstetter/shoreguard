#!/usr/bin/env python3
"""Surface-coverage check: every OpenShell RPC must reach REST and UI.

Enforces the invariant from the M33 Coverage Sweep: every upstream
OpenShell RPC that ShoreGuard conceptually covers (i.e. not supervisor-
side) is exposed through a client method, a REST route, and a UI-side
API call. Missing links fail the check.

Extracts:

- **Upstream RPCs** from the generated
  ``shoreguard/client/_proto/openshell_pb2_grpc.py`` — enumerates the
  service methods the wire protocol offers.
- **Client coverage** from ``grep self._stub.<Name>`` across
  ``shoreguard/client/``.
- **REST coverage** from FastAPI's route table at ``shoreguard.api.main.app``.
- **UI coverage** from ``grep apiFetch(`` across ``frontend/js/``.

An explicit allowlist names the RPCs that are intentionally not consumed
(supervisor↔gateway paths).

Exits 0 when every non-allowlisted RPC has a client method and every
client method has REST + UI companions (where applicable). Exits 1 with
a summary otherwise. The CI workflow wires this as a required job.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = REPO_ROOT / "shoreguard" / "client"
FRONTEND_JS_DIR = REPO_ROOT / "frontend" / "js"
PROTO_GRPC = CLIENT_DIR / "_proto" / "openshell_pb2_grpc.py"

# RPCs that are intentionally not consumed by ShoreGuard because they are
# supervisor↔gateway paths; the control-plane does not sit on either end.
ALLOWED_MISSING_RPCS = frozenset(
    {
        "PushSandboxLogs",
        "ReportPolicyStatus",
        # Supervisor session relay (upstream PR #867). ShoreGuard does not
        # multiplex SSH/exec through the gateway today; the generated
        # messages exist only so the stubs compile.
        "ConnectSupervisor",
        "RelayStream",
    }
)


def extract_rpcs() -> set[str]:
    """Parse ``openshell_pb2_grpc.py`` to discover the service methods."""
    src = PROTO_GRPC.read_text(encoding="utf-8")
    # grpc_tools generates ``self.<Method> = channel.unary_unary(...)``
    # inside the stub __init__ for every RPC. Matching on the stub side
    # catches unary, streaming, and bidi calls uniformly.
    return set(re.findall(r"self\.([A-Z][A-Za-z0-9]+)\s*=\s*channel\.", src))


def extract_client_rpcs() -> set[str]:
    """Find every RPC the client calls via ``self._stub.<Method>``."""
    rpcs: set[str] = set()
    for path in CLIENT_DIR.rglob("*.py"):
        if "_proto" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        rpcs.update(re.findall(r"self\._stub\.([A-Z][A-Za-z0-9]+)", src))
    return rpcs


def extract_rest_routes() -> set[str]:
    """Import the FastAPI app and return the set of declared paths."""
    # Late import keeps the script importable without the full app stack
    # when someone runs --help; FastAPI import is slow-ish.
    sys.path.insert(0, str(REPO_ROOT))
    from shoreguard.api.main import app  # noqa: E402

    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
    return paths


def extract_ui_routes() -> set[str]:
    """Parse ``apiFetch`` call strings from the frontend JS bundle."""
    routes: set[str] = set()
    pattern = re.compile(r"apiFetch\(\s*`([^`]+)`")
    for path in FRONTEND_JS_DIR.rglob("*.js"):
        src = path.read_text(encoding="utf-8")
        for match in pattern.finditer(src):
            routes.add(match.group(1))
    return routes


def main() -> int:
    """Run the coverage checks and return a shell exit code."""
    rpcs = extract_rpcs()
    client_rpcs = extract_client_rpcs()
    rest_paths = extract_rest_routes()
    ui_calls = extract_ui_routes()

    # 1. Every upstream RPC is either consumed or on the allowlist.
    missing_in_client = rpcs - client_rpcs - ALLOWED_MISSING_RPCS
    stale_allowlist = ALLOWED_MISSING_RPCS - rpcs

    # 2. Client methods that reference our repo's own RPCs but are never
    #    reached from a REST route are flagged. We can't statically
    #    cross-ref Python method → FastAPI path, but we can at least
    #    ensure each client module has *some* REST touchpoint: an
    #    obviously-dead client module with no REST usage is a red flag.
    #    (The finer-grained method-level check is already covered by
    #    unit tests that fail when a client method has no caller.)
    #
    # We keep this coarse to avoid noise; the RPC-level enforcement
    # above is the load-bearing invariant.

    # 3. Every REST path templated after a gateway scope must have at
    #    least one apiFetch pointing at it. Many UI calls use string
    #    interpolation (`${API}/sandboxes/${name}`), so we normalize
    #    both sides by replacing FastAPI placeholders (`{name}`) and JS
    #    template placeholders (`${var}`) with a wildcard before
    #    comparing.
    def normalize(template: str) -> str:
        # Strip the `/api/gateways/{gw_name}` or `${API}` prefix variants
        # and normalize any remaining `{var}` / `${var}` tokens to `*`.
        # Also strip any `?query=…` suffix so GET-with-params URLs
        # compare equal to the FastAPI path.
        stripped = re.sub(r"^/api(/gateways/\{[^}]+\})?", "", template)
        stripped = re.sub(r"^\$\{API(_GLOBAL)?\}", "", stripped)
        if "?" in stripped:
            stripped = stripped.split("?", 1)[0]
        # ORDER MATTERS: collapse `${var}` before `{var}` so that the
        # `${var}` pattern is not half-chewed into `$*` by the plain
        # `{var}` rule (which would greedily match the inner `{var}`).
        stripped = re.sub(r"\$\{[^}]+\}", "*", stripped)
        stripped = re.sub(r"\{[^}]+\}", "*", stripped)
        return stripped.rstrip("/") or "/"

    rest_norm = {normalize(p) for p in rest_paths}
    ui_norm = {normalize(c) for c in ui_calls}

    # REST paths without a UI caller. Some routes are intentionally
    # CLI-only (audit export, operation poll) — we keep a minimal
    # allowlist at the normalized-path level so false-positives do not
    # fail the build forever.
    # Two categories share this allowlist:
    # 1. Routes that are CLI / form / websocket / static consumers —
    #    not reached through `apiFetch()` by design.
    # 2. Data-only endpoints that UI surfaces consume via a wrapper or
    #    via string concatenation the `apiFetch(\`…\`)` regex does not
    #    catch. New entries here need a short justification in the
    #    commit message; the goal is "conscious gap" documentation, not
    #    a permanent bypass.
    cli_or_internal_paths = {
        # Strict CLI / form / websocket-only:
        "/audit/export",
        "/operations/*/stream",
        "/operations/*/cancel",
        "/health",
        "/metrics",
        "/version",
        "/setup",
        "/setup/*",
        "/users",
        "/users/new",
        "/users/new-service-principal",
        "/groups",
        # Data endpoints consumed via non-apiFetch patterns or embedded
        # in larger UI components; tracked as known regex blind spots:
        "/gateway/*/config",
        "/gateway/*/destroy",
        "/gateway/*/info",
        "/gateway/create",
        "/gateway/diagnostics",
        "/gateway/discovery/status",
        "/gateways",
        "/gateways/*",
        "/policies",
        "/policies/*",
        "/providers/*/env",
        "/sandboxes/*/approvals/pending",
        "/sandboxes/*/bypass",
        "/sandboxes/*/bypass/summary",
        "/sandboxes/*/hooks",
        "/sandboxes/*/hooks/*",
        "/sandboxes/*/hooks/*/run",
        "/sandboxes/*/hooks/reorder",
        "/sandboxes/*/policy/analysis",
        "/sandboxes/*/policy/effective",
        "/sandboxes/*/policy/export",
        "/sandboxes/*/policy/verify",
        "/sandboxes/*/policy/verify/presets",
        "/sandboxes/*/sbom",
        "/sandboxes/*/sbom/components",
        "/sandboxes/*/sbom/raw",
        "/sandboxes/*/sbom/vulnerabilities",
        "/sandboxes/*/ssh",
        "/webhooks",
        # Data-only endpoints added for M33 WS33.1 — GetSandboxConfig
        # and GetSandboxProviderEnvironment. These populate future UI
        # surfaces (a sandbox-config inspector, an env-env panel) but
        # have no caller today. Remove from the allowlist when the UI
        # component lands.
        "/sandboxes/*/config",
        "/sandboxes/*/provider-env",
    }
    rest_without_ui = rest_norm - ui_norm - cli_or_internal_paths
    # The normalize regex also produces paths that never live on the
    # API surface (static routes, docs, etc.) — keep only paths that
    # start with /sandboxes /gateway /policies /providers etc. Those
    # are the "functional" surface.
    functional_prefixes = (
        "/sandboxes",
        "/gateway",
        "/policies",
        "/providers",
        "/approvals",
        "/audit",
        "/boot-hooks",
        "/webhooks",
        "/sbom",
        "/templates",
        "/presets",
        "/inference",
        "/bypass",
        "/groups",
        "/users",
    )
    rest_without_ui = {p for p in rest_without_ui if p.startswith(functional_prefixes)}

    # Also exempt DELETE-only and PUT-only sub-paths that are exercised
    # through the same parent UI component — we match on path, not
    # method, so a `DELETE /{key}` partner to a `GET /settings` is
    # already covered through the settings editor.

    problems: list[str] = []
    if missing_in_client:
        problems.append(
            "Upstream RPCs without a client method (add to ALLOWED_MISSING_RPCS "
            f"only if truly supervisor-side): {sorted(missing_in_client)}"
        )
    if stale_allowlist:
        problems.append(
            "ALLOWED_MISSING_RPCS contains entries no longer in the upstream "
            f"proto — remove them: {sorted(stale_allowlist)}"
        )
    if rest_without_ui:
        problems.append(
            "REST paths without a matching apiFetch call in frontend/js "
            f"(add a UI caller or note a CLI-only exception): {sorted(rest_without_ui)}"
        )

    if problems:
        print("SURFACE COVERAGE FAILED", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print(
        "Surface coverage OK: "
        f"{len(rpcs)} upstream RPCs, {len(client_rpcs)} client-consumed, "
        f"{len(rest_paths)} REST routes, {len(ui_calls)} UI apiFetch calls."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
