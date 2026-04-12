# Approval Workflows (Quorum)

Single-voter approvals work fine for a one-person team. For anything
larger, production changes should require multiple sign-offs with a
clear escalation path when a voter is unavailable. ShoreGuard's
**Approval Workflows** feature adds quorum-based multi-stage
approvals per sandbox.

For the basic one-voter approval flow, see [Approvals](approvals.md).
This guide covers only the M19 multi-stage extension.

## Model

An `ApprovalWorkflow` attaches to a `(gateway, sandbox)` pair. It
defines:

- A set of eligible voters (users or groups).
- A `required_votes` quorum threshold (must be ≥ 2 for the
  workflow to be meaningful).
- An optional `escalation_deadline_seconds` — after this interval
  elapses without quorum, the next vote fires an
  `approval.escalated` webhook so an on-call human is paged.
- A unanimity rule for rejection: a single `reject` kills the
  proposal immediately, regardless of the current tally.

When a workflow is active, `POST
/api/gateways/{gw}/approvals/{chunk_id}/approve` returns:

- **HTTP 202 `vote_recorded`** until quorum is reached — the upstream
  `ApproveChunk` has **not** been called yet.
- **HTTP 200 `approved`** on the vote that reaches quorum. The
  upstream `ApproveChunk` fires exactly once.
- **HTTP 403** if the caller is not an eligible voter.
- **HTTP 409** on double-voting from the same caller.

`POST /approve-all` is admin-only when a workflow is active —
non-admins receive HTTP 409. This is the emergency override path.

## Configuring a workflow

Via API:

```http
PUT /api/gateways/dev/sandboxes/agent-a/approval-workflow
Content-Type: application/json

{
  "required_votes": 2,
  "voters": ["alice@example.com", "bob@example.com", "carol@example.com"],
  "escalation_deadline_seconds": 1800
}
```

Via Terraform (M24):

```hcl
resource "shoreguard_approval_workflow" "prod" {
  gateway                       = "dev"
  sandbox_name                  = "agent-a"
  required_votes                = 2
  voters                        = ["alice@example.com", "bob@example.com"]
  escalation_deadline_seconds   = 1800
}
```

Via the UI: open the sandbox detail page, click **Workflow** in the
admin menu, pick voters + quorum, save.

## Observing votes

`GET /api/gateways/{gw}/approvals/{chunk_id}/decisions` returns the
running tally + voter list for a specific pending approval. The
approval detail modal in the UI renders the same data with a "Vote
to approve" button that disables itself after the current user has
voted.

## Webhook events

| Event | When |
|---|---|
| `approval.vote_cast` | Any vote (approve *or* reject) |
| `approval.quorum_met` | The vote that reaches the required count |
| `approval.escalated` | First vote after the escalation deadline elapsed |

Escalation is **reactive**, not scheduled — there is no background
loop. The next vote after the deadline checks the clock and fires
the webhook. This keeps the system simple and avoids a second moving
part; in practice the on-call team is already subscribed to
`approval.vote_cast` and notices the delay.

## Interaction with M23 GitOps

When a workflow is active, `POST /policy/apply` counts as **one
vote** on a synthetic chunk id `policy.apply:<sha16>`. Subsequent
apply calls with the same YAML body accumulate votes until quorum.
This lets a CI pipeline participate in quorum without special
handling — the second human voter just approves from the UI.

## Reference

- API: [`GET|PUT|DELETE /approval-workflow`](../reference/api.md#approval-workflows-m19-v0302)
- Policy pinning interaction: a pinned sandbox (M18) blocks all
  approvals with HTTP 423 *regardless* of workflow state.
