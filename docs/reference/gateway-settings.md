# Gateway Settings Reference

Settings keys writable through the **OpenShell gateway** via
`PUT /api/gateway/{name}/settings/{key}`. These are *runtime* feature flags
stored on the gateway (not ShoreGuard env vars — see
[Settings Reference](settings.md) for those).

The list is the upstream registry — ShoreGuard does not maintain its own
allow-list. The Wizard UI in `frontend/js/gateway.js` reads the keys
generically, so any new entry the gateway adds appears automatically.

**Source of truth:**
[`crates/openshell-core/src/settings.rs`](https://github.com/NVIDIA/OpenShell/blob/57a80ed2/crates/openshell-core/src/settings.rs)
in NVIDIA/OpenShell. The table below mirrors `REGISTERED_SETTINGS` at
commit `57a80ed2` (post-`v0.0.37`).

## Registered keys

| Key | Kind | Scope | Default | Since | Purpose |
|---|---|---|---|---|---|
| `providers_v2_enabled` | bool | gateway | `false` | v0.35.0 (M37) | Enables the Provider-Profile registry surface (`/provider-profiles`). When false, profile-import RPCs return `FAILED_PRECONDITION`. |
| `ocsf_json_enabled` | bool | sandbox | `false` | v0.34.2 | When true the sandbox writes OCSF v1.7.0 JSONL records to `/var/log/openshell-ocsf*.log` (daily rotation, 3 files) in addition to the human-readable shorthand log. |
| `agent_policy_proposals_enabled` | bool | sandbox | `false` | v0.35.0 (M37) | Enables the agent-driven policy proposal surface inside the sandbox: the `policy_advisor` skill, the `policy.local` API, and `next_steps` hints in L7 deny bodies. Independent of the per-proposal developer approval gate, which still applies when this flag is on. |

The gateway exposes the registered keys CSV-list via `registered_keys_csv()`
in upstream — and surfaces it as a 422 `details` field when an unknown key
is rejected.

## Setting a key

Via REST:

```bash
curl -X PUT \
  -H 'Content-Type: application/json' \
  -d '{"value": true}' \
  http://localhost:8888/api/gateway/<gw>/settings/providers_v2_enabled
```

Via the Wizard UI: navigate to `Gateways → <gw> → Settings`, toggle the
key, save. Persistence is gateway-side; no ShoreGuard restart needed.

## Dev-only keys

Behind the upstream `dev-settings` cargo feature flag (not present in
release builds):

- `dummy_int` (int)
- `dummy_bool` (bool)

These are used for upstream integration tests only and will be rejected
by a production-built gateway.

## Adding a new key

New keys land in OpenShell's `REGISTERED_SETTINGS` array. After upgrading
the gateway, ShoreGuard picks them up automatically — no client/REST/UI
change is needed *unless* the key needs special UI affordance (e.g. a
typed picker beyond bool/int/string). Update this page after the
upstream PR merges.
