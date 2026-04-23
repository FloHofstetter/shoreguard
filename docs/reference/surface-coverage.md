# Surface Coverage

ShoreGuard holds a simple discipline: **every OpenShell wire feature that
is meaningful for a control-plane reaches the client, the REST API, and
the UI**. This page documents the invariant and the enforcement script
that keeps it honest.

## Coverage invariant

Three layers:

| Layer | Surface | Source |
| --- | --- | --- |
| **Wire** | gRPC method on `OpenShellStub` | `shoreguard/client/_proto/openshell_pb2_grpc.py` |
| **REST** | FastAPI route on `shoreguard.api.main.app` | `shoreguard/api/routes/*.py` |
| **UI** | `apiFetch(...)` call in a JS module | `frontend/js/*.js` |

A functional feature is "covered" when an upstream RPC has a client
method (`self._stub.<Rpc>(...)`), and that client method is reachable
from a REST route, and that REST route is reached from a UI caller. The
chain is what makes a feature *usable*, not just *built*.

## The enforcement script

`scripts/check_coverage.py` ([source](https://github.com/FloHofstetter/shoreguard/blob/main/scripts/check_coverage.py))
asserts
the invariant and is run as a required CI job. It:

1. Enumerates all upstream RPCs from the generated `_pb2_grpc.py`.
2. Compares against RPCs consumed by the client (`grep self._stub.`).
3. Loads the FastAPI app and reads its route table.
4. Extracts `apiFetch(…)` URLs from the frontend bundle.
5. Fails the build on:
   - any upstream RPC without a client method **and** not on the
     supervisor-path allowlist;
   - any REST route not matched by a UI caller **and** not on the
     CLI/data-only allowlist.

Two allowlists encode conscious gaps:

- `ALLOWED_MISSING_RPCS` — supervisor-to-gateway RPCs the control-plane
  never consumes by design (`PushSandboxLogs`, `ReportPolicyStatus`,
  `ConnectSupervisor`, `RelayStream`).
- `cli_or_internal_paths` — REST paths that are CLI-only, form-posted,
  websocket, static, or reached through a non-`apiFetch` pattern the
  regex does not catch.

Adding an entry to either list requires a short commit-message
justification; the allowlist is the "conscious gap" audit trail, not a
permanent bypass.

## What breaks the check

These failure modes are intentional:

- **New upstream RPC without client coverage.** After a stub regen, the
  script flags the new method. Add it to the relevant client module or,
  if it is a supervisor-side RPC, append it to `ALLOWED_MISSING_RPCS`
  with a line-of-reason in the commit message.
- **New REST route without UI caller.** The script flags the normalized
  path. Either land the UI component in the same release, or note it
  under `cli_or_internal_paths` with the reason it is not UI-reachable
  (e.g. "scheduled-task trigger, not end-user operation").
- **Removed upstream RPC.** The stale entry in `ALLOWED_MISSING_RPCS`
  fails the check; remove it.

## Running locally

```bash
uv run python scripts/check_coverage.py
```

Expected output on success:

```
Surface coverage OK: 34 upstream RPCs, 30 client-consumed,
131 REST routes, 70 UI apiFetch calls.
```

On failure, the script prints a summary to stderr and exits non-zero,
matching the CI job's behaviour.

## Current state

As of v0.34.0:

- 34 upstream RPCs; 30 consumed by the client (4 allowlisted as
  supervisor-path).
- Every non-supervisor RPC has a client method that unit tests
  exercise.
- REST surface covers every client method that is meaningful for an
  external caller (gateway-internal wrappers, resilience helpers, and
  the `close()` cleanup hook are not REST-exposed by design).
- UI coverage is tracked path-by-path; gaps in the `cli_or_internal_paths`
  list are all documented.

## History

The coverage enforcement was introduced in M33 (WS33.6) as the
companion discipline to the "functional coverage sweep" that closed the
then-known gaps (GetSandboxConfig / GetSandboxProviderEnvironment REST
routes; allow_encoded_slash toggle, apply-mode toggle, drift indicator,
and advanced gateway settings in the UI). The invariant is enforced
from v0.34.0 onwards — earlier releases did not have the script and
accumulated several silent gaps.
