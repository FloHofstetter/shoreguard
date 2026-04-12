# Policy Pinning

A policy pin freezes the active policy version of a sandbox. While a
pin is in effect, every write path (policy edits, preset apply,
network/filesystem CRUD, approvals, approve-all, GitOps apply)
returns **HTTP 423 Locked**. Reads are unaffected.

## What it solves

During an incident or a change freeze, you want a hard guarantee that
nobody — not a CI pipeline, not a well-meaning operator, not a
scheduled GitOps sync — rewrites a sandbox's policy behind your
back. Comments and conventions are not enough. The pin is enforced
server-side in one place, so every entry point respects it.

## Pinning a sandbox

From the sandbox detail page, click **Lock policy**. Enter a reason
(free text) and an optional expiry timestamp. Pins auto-expire
server-side at the expiry — no need to unpin manually.

Via API:

```http
POST /api/gateways/dev/sandboxes/agent-a/policy/pin
Content-Type: application/json

{
  "reason": "Change freeze for release-2026-04",
  "expires_at": "2026-04-20T00:00:00Z"
}
```

Via Terraform (M24):

```hcl
resource "shoreguard_policy_pin" "freeze" {
  gateway      = "dev"
  sandbox_name = "agent-a"
  reason       = "Change freeze for release-2026-04"
  expires_at   = "2026-04-20T00:00:00Z"
}
```

## What a pinned sandbox looks like

- A red banner on every policy sub-page (network, filesystem,
  process, presets, approvals) with the pin reason + expiry.
- Every edit button disabled.
- Any write API returns HTTP 423 with
  `code: policy_locked` in the problem-details body.
- `POST /approve` and `/approve-all` also return HTTP 423 — you
  cannot approve a denial while the policy is pinned, even if the
  approval flow itself is open.
- `GET /export` (M23) still works — export is read-only.

## Security-flagged rules

On the same page, policy chunks that OpenShell marks as
security-flagged now render with a red shield badge. The filter chip
"Show flagged only" narrows the list; the "Approve All"
confirmation dialog carries an explicit **include flagged**
checkbox so flagged rules cannot be bulk-approved by accident.

## Reference

- API: [`GET|POST|DELETE /api/gateways/{gw}/sandboxes/{name}/policy/pin`](../reference/api.md#policy-pinning-m18-v0302)
- Audit events: `policy_pin.created`, `policy_pin.deleted`.
