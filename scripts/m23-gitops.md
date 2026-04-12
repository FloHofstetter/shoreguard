# M23 — GitOps Policy Sync runbook

ShoreGuard ships a declarative policy export/apply API and a `shoreguard policy` CLI so a CI pipeline can drive sandbox policy changes from a Git repo. This runbook walks the full feature set.

## Prereqs

- ShoreGuard ≥ v0.30.2 with at least one registered gateway and one sandbox.
- `SHOREGUARD_URL` set to the base URL (default `http://localhost:8888`).
- `SHOREGUARD_TOKEN` set to a session/Bearer token if auth is enabled.
- `pyyaml` and `httpx` available (already part of the ShoreGuard runtime).

## CLI commands

```sh
shoreguard policy export --gateway <gw> --sandbox <name> --output sandboxes/<name>.yaml
shoreguard policy diff   --gateway <gw> --sandbox <name> --file sandboxes/<name>.yaml
shoreguard policy apply  --gateway <gw> --sandbox <name> --file sandboxes/<name>.yaml
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Up-to-date / successful apply |
| `1` | Drift detected (dry-run) or vote recorded under a workflow |
| `2` | Operational error (network, 4xx, parse) |

`--file -` reads YAML from stdin.

## YAML layout

Export emits a self-describing document:

```yaml
# managed-by: shoreguard-gitops
metadata:
  gateway: prod
  sandbox: web-api
  version: 7
  policy_hash: sha256:abc123…
  exported_at: 2026-04-12T00:00:00+00:00
policy:
  filesystem:
    include_workdir: true
    read_only: [/usr, /etc]
    read_write: [/tmp]
  process:
    run_as_user: app
    run_as_group: app
  network_policies:
    anthropic:
      name: anthropic
      endpoints:
        - {host: api.anthropic.com, port: 443}
```

`metadata.policy_hash` is the etag for optimistic locking — apply will reject the change with HTTP 409 if the live hash differs (someone else edited in the meantime). Hand-written YAMLs without a metadata block apply unconditionally.

## CI integration sketch

```yaml
- name: Diff policy
  run: shoreguard policy diff -g prod -s web-api -f sandboxes/web-api.yaml
- name: Apply policy
  run: shoreguard policy apply -g prod -s web-api -f sandboxes/web-api.yaml
```

A diff with drift exits 1 → mark the workflow as failed and require a follow-up commit. A clean diff exits 0 → policy is in sync.

## Pin interaction (M18)

Apply (and dry-run!) returns HTTP 423 on a pinned sandbox. Unpin first or wait for the pin to expire. Export remains allowed because it is a pure read.

## Workflow interaction (M19)

When an active multi-stage approval workflow is configured for the sandbox, an apply that would change anything is gated:

1. First call records one approve-vote on a synthetic chunk id `policy.apply:<sha16>` (first 16 hex chars of SHA-256 over the YAML body), returns HTTP 202 with `{"status":"vote_recorded","votes_cast":1,"votes_needed":N}`. CLI exits 1.
2. Subsequent calls (different actor + same YAML body → same `chunk_id`) record additional votes. On quorum the upstream `UpdateConfig` fires once. CLI exits 0.

The pending YAML body is held in `policy_apply_proposals` (Alembic 017) so the second voter does not need to resubmit bytes.

## Drift detection (optional)

The drift loop is **off by default**. Enable per-process via env:

```sh
export SHOREGUARD_DRIFT_DETECTION_ENABLED=true
export SHOREGUARD_DRIFT_DETECTION_INTERVAL_SECONDS=300
```

When on, ShoreGuard polls every registered sandbox every interval and fires a `policy.drift_detected` webhook on any hash change between scans. The first scan after restart is a no-op (snapshot bootstrap). The signal means: someone edited the policy outside the GitOps pipeline. Wire it to a Slack/PagerDuty webhook to know within minutes.

Drift detection only observes — it never auto-reverts. Auto-revert would fight the M19 quorum model.

## Webhook events

| Event | Fired by | Payload keys |
|---|---|---|
| `policy.applied` | `POST /policy/apply` write branch | `gateway`, `sandbox`, `actor`, `applied_version`, `diff_summary` |
| `policy.drift_detected` | drift loop | `gateway`, `sandbox`, `previous_hash`, `current_hash`, `detected_at` |
| `approval.vote_cast` | apply under workflow (no quorum yet) | `gateway`, `sandbox`, `actor`, `chunk_id`, `scope: policy.apply` |
| `approval.quorum_met` | apply under workflow (quorum just reached) | `gateway`, `sandbox`, `chunk_id`, `votes_needed`, `scope: policy.apply` |

## Audit events

`policy.exported`, `policy.apply.dry_run`, `policy.apply.noop`, `policy.apply.voted`, `policy.applied`, `policy.drift_detected`.

## Demo script

`scripts/m23_demo.py` runs eight phases against a live local stack: export → no-op → dry-run drift → write → workflow vote → workflow quorum → pin 423 → drift hint. Takes ~30 seconds.
