# GitOps Policy Sync

The M23 GitOps flow lets you manage sandbox policies as YAML in a
Git repo and apply them via CI/CD with the same safety rails the UI
uses — pins, quorum workflows, audit log, optimistic locking.

## Why a separate flow from Terraform?

Terraform provisions infrastructure — gateways, groups, approval
workflows, pins. Policy **content** changes on a different cadence
(every denial flow, often daily) and should route through the same
quorum as a manual policy edit. Putting policy content in Terraform
state would drift every time a denial gets approved, so the M24
Terraform provider deliberately does **not** expose a
`shoreguard_sandbox_policy` resource. GitOps fills that gap.

## The round-trip

`GET /export` produces a deterministic YAML document:

```yaml
metadata:
  gateway: dev
  sandbox: agent-a
  version: 42
  policy_hash: 8f3a…
  exported_at: 2026-04-13T12:00:00Z
policy:
  network:
    rules: [...]
  filesystem:
    paths: [...]
  process: {...}
```

Re-exporting a YAML that came out of `GET /export` produces the
same bytes (the ordering is stable and keys are sorted). That
matters because your Git diff stays signal, not noise.

`POST /apply` takes `{yaml, dry_run, expected_version}` and returns
one of:

| Status | Meaning |
|---|---|
| `200 up_to_date` | Submitted YAML matches the live policy. No write. |
| `200 dry_run` | Dry-run apply — computed the diff, did not write. |
| `200 applied` | Policy updated upstream. |
| `202 vote_recorded` | M19 workflow active — one vote recorded, no write yet. |
| `409` | `expected_version` / `policy_hash` does not match the live version. |
| `423` | Sandbox is pinned (M18). |
| `400` | Malformed YAML. |

**Optimistic locking:** if you omit `expected_version`, it falls
back to `metadata.policy_hash` in the YAML document. A mismatch
returns the live `current_hash` in the response body so CI can
refetch + retry without a second roundtrip.

## CLI

`shoreguard policy` wraps all three operations:

```bash
# Dump the live policy to stdout
shoreguard policy export --gateway dev --sandbox agent-a > policy.yaml

# Diff (dry-run apply) — exits 1 on drift, 0 if up-to-date
shoreguard policy diff --gateway dev --sandbox agent-a -f policy.yaml

# Apply (writes) — exits 1 if a vote was recorded but quorum not met,
# exit 2 on any error
shoreguard policy apply --gateway dev --sandbox agent-a -f policy.yaml
```

Credentials: `SHOREGUARD_URL` + `SHOREGUARD_TOKEN` env vars or
`--url` / `--token` flags.

## Typical CI pipeline

```yaml
# .github/workflows/policy-sync.yml
- run: pip install shoreguard-cli
- run: shoreguard policy diff --gateway dev --sandbox agent-a -f agent-a.yaml
- run: shoreguard policy apply --gateway dev --sandbox agent-a -f agent-a.yaml
  if: github.ref == 'refs/heads/main'
```

Under a quorum workflow, the `apply` on the main branch returns
exit code 1 + `vote_recorded`. The second human voter approves from
the UI, which reaches quorum and fires `UpdateConfig` upstream
exactly once. CI does not need to re-run.

## Drift detection (optional)

`DriftDetectionService` is a background loop, off by default behind
`SHOREGUARD_DRIFT_DETECTION_ENABLED`. When enabled, it polls every
registered sandbox every interval, compares the policy hash to the
last snapshot, and fires a `policy.drift_detected` webhook on any
change between scans. The first scan after restart bootstraps the
snapshot silently. Per-sandbox failures are logged + swallowed.

Subscribe to the webhook from Slack and every out-of-band policy
edit becomes a visible event, not a mystery.

## Interaction with pins and workflows

- **Pinned sandbox (M18)** — `apply` and `dry_run` both return
  HTTP 423. Export stays allowed.
- **Active workflow (M19)** — the first `apply` records one
  approve-vote on a synthetic chunk id `policy.apply:<sha16>`. A
  new table `policy_apply_proposals` caches the pending YAML so
  the second voter does not need to resubmit bytes. Subsequent
  apply calls with the same YAML body accumulate votes until quorum.

## Reference

- API: [`/api/gateways/{gw}/sandboxes/{name}/policy/export` + `/apply`](../reference/api.md#gitops-m23-v0302)
- Demo: `scripts/m23_demo.py` (8 phases: export → no-op → drift →
  write → vote → quorum → pin → drift).
- Runbook: `scripts/m23-gitops.md`.
- Audit events: `policy.exported`, `policy.apply.dry_run`,
  `policy.apply.noop`, `policy.apply.voted`, `policy.applied`,
  `policy.drift_detected`.
