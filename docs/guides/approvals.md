# Approval Flow

## How it works

When an AI agent inside a sandbox tries to access a blocked endpoint, OpenShell
does not simply reject the request. Instead, it generates a **draft policy
recommendation** — a set of rules that would allow the access if approved.

ShoreGuard surfaces these recommendations as **pending approval chunks** in
the Web UI and pushes real-time notifications via WebSocket.

## Reviewing approvals

Each pending chunk shows the endpoint, method, path, and the suggested action.
You can take any of the following actions:

| Action | Effect |
|--------|--------|
| **Approve** | Accept the rule and merge it into the sandbox policy |
| **Reject** | Discard the recommendation |
| **Edit** | Modify the rule before approving (e.g., narrow the path) |
| **Approve All** | Accept all pending chunks at once |
| **Undo** | Revert the last approval or rejection |
| **Clear** | Dismiss all pending chunks without taking action |

## Real-time notifications

When a new approval chunk arrives, ShoreGuard displays a toast notification in
the browser. The approvals badge in the navigation bar updates automatically
so you never miss a pending request.

## API endpoints

All approval actions are available via the REST API:

| Endpoint | Description |
|----------|-------------|
| `GET /pending` | List all pending approval chunks for a sandbox |
| `POST /approve` | Approve a specific chunk |
| `POST /reject` | Reject a specific chunk |
| `POST /edit` | Edit and approve a modified chunk |
| `POST /approve-all` | Approve all pending chunks |
| `POST /undo` | Undo the last action |
| `POST /clear` | Clear all pending chunks |

Both `POST /approve` and `POST /approve-all` accept
`?wait_loaded=true`, which makes the server block until the new
policy version is actually loaded on the gateway (up to 30 s, 504
on timeout). This avoids the client-side polling loop that was
previously needed to dodge spurious 403s from a proxy still running
the old policy.

## Binary-Context Approvals (M16)

Since v0.30.2, each pending chunk carries richer denial context so
reviewers can decide with full evidence rather than hash alone:

- **Process ancestry breadcrumb** — the full parent chain of the
  binary that hit the denial, rendered inline on the approval
  detail modal.
- **Binary SHA-256 badge** — the hash of the binary that triggered
  the denial, so an operator can cross-check against their SBOM
  (see the [SBOM guide](sbom.md)) or an allowlist.
- **Persistent-context badge** — flagged when the same binary has
  requested approval for the same chunk before. This is a strong
  hint that you are looking at a recurring pattern, not a one-off.
- **L7 request samples** — up to 10 recent requests that matched
  the denial (method, path, status, source), so reviewers can see
  the actual traffic the rule is about to permit.
- **"Persistent first" sort toggle** on the pending list, persisted
  per browser via `localStorage`.

Context is captured at `submit_analysis` time into the
`DenialContextService` in-memory TTL cache, then enriched at
`get_draft` so the approval modal never has to wait on a gateway
round-trip.

## Multi-Stage Workflows

For quorum-based approvals (multiple required sign-offs, escalation
deadlines), see [Approval Workflows](approval-workflows.md).

## Policy Pinning

While a sandbox is pinned (see [Policy Pinning](policy-pinning.md)),
all approval actions return **HTTP 423 Locked** — the pin wins over
any pending approvals.
