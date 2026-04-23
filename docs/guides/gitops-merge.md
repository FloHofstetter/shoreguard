# GitOps Incremental Merge Mode

`POST /sandboxes/{name}/policy/apply` accepts a `mode` field that
controls how ShoreGuard writes the target policy onto the gateway:

| `mode` | How it writes | Requires gateway |
| --- | --- | --- |
| `replace` (default) | Serialises the full target policy and sends it as `UpdateConfigRequest.policy`. Equivalent to the historical behaviour. | `≥ v0.0.30` |
| `merge` | Diffs the target against the currently-enforced policy, emits a sequence of `PolicyMergeOperation`s, and sends them as `UpdateConfigRequest.merge_operations`. | `≥ v0.0.33` |

## When to use which

- **`replace`** — changes to `filesystem`, `process`, `landlock`, or
  any cross-section edit. Also the right choice for the first apply
  against a freshly-provisioned sandbox when no current policy exists.
- **`merge`** — `network_policies`-only edits where you want
  audit logs that read like Git diffs ("add `allow-gh`, remove
  `allow-legacy`") instead of full-policy blobs. Natural fit for CI
  pipelines that apply small deltas frequently.

ShoreGuard rejects `mode=merge` with **HTTP 400** when the diff
touches `filesystem`, `process`, `landlock`, or an explicit version
bump — the upstream `PolicyMergeOperation` oneof cannot express those
changes. The response body is:

```json
{
  "detail": {
    "status": "merge_unsupported",
    "reason": "mode=merge cannot express changes to the 'filesystem' section…",
    "hint": "retry with mode='replace' for changes outside network_policies"
  }
}
```

CI can detect the status code and re-issue with `mode=replace`.

## The six merge operations

| Operation | Semantics |
| --- | --- |
| `add_rule` | Insert or replace a whole network rule by `rule_name`. |
| `remove_rule` | Drop a whole network rule by `rule_name`. |
| `remove_endpoint` | Remove a specific `(host, port)` endpoint from a named rule. |
| `add_allow_rules` | Append L7 allow rules to a specific `(host, port)` endpoint. |
| `add_deny_rules` | Append L7 deny rules to a specific `(host, port)` endpoint. |
| `remove_binary` | Remove a binary path from a named rule. |

ShoreGuard's diff engine today emits **only** `add_rule` + `remove_rule`
— rule-body changes are expressed as a `remove_rule` + `add_rule`
pair rather than per-endpoint edits. That keeps the audit log readable
and the semantics atomic on the gateway. The finer-grained operations
are reachable for manual callers that build the op list directly (for
example, an approval workflow that stores just the diff); a future
iteration may add them to the diff engine.

## Ordering invariant

The diff engine places every `remove_*` operation before any `add_*`
operation. This matters because the gateway applies the list in order
and a partial failure must not leave the policy with duplicated
endpoints across two revisions.

## CLI

```bash
shoreguard policy apply \
  --gateway my-gw --sandbox sb1 \
  --file ./policies/sb1.yaml \
  --mode merge
```

`--mode replace` (the default) is always safe; `--mode merge`
fails fast if the gateway is older than `v0.0.33` (gRPC
`UNIMPLEMENTED`) or if the diff touches a non-mergeable section
(HTTP 400 per above).

## Audit

Every apply records `apply_mode` in the audit detail. Merge applies
additionally record `merge_operation_count` so an operator reviewing
the log can tell a 3-op merge apart from a full-rewrite.

## Example: YAML diff → merge ops

Given a current policy with one rule:

```yaml
policy:
  network_policies:
    allow-legacy:
      name: allow-legacy
      endpoints:
        - {host: legacy.example.com, port: 443}
```

and a target that adds one new rule and drops the legacy one:

```yaml
policy:
  network_policies:
    allow-gh:
      name: allow-gh
      endpoints:
        - {host: api.github.com, port: 443}
```

The diff engine emits, in order:

```json
[
  {"type": "remove_rule", "rule_name": "allow-legacy"},
  {"type": "add_rule", "rule_name": "allow-gh", "rule": {"name": "allow-gh", "endpoints": [...]}}
]
```

The gateway applies them atomically and assigns a single new policy
revision.
