# Approval Flow

## How it works

When an AI agent inside a sandbox tries to access a blocked endpoint, OpenShell
does not simply reject the request. Instead, it generates a **draft policy
recommendation** — a set of rules that would allow the access if approved.

ShoreGuard surfaces these recommendations as **pending approval chunks** in
the Web UI and pushes real-time notifications via WebSocket.

## The approval inbox (all gateways)

The **Approvals** page (`/approvals` in the sidebar) is a single, cross-gateway
list of every pending chunk across all sandboxes and all reachable gateways —
security-flagged ones first, then ranked by hit-count and confidence. Each row
shows the rule, the sandbox · gateway · binary, the proposed endpoints, and the
confidence, with **Approve** / **Reject** in place (quorum-aware — a vote on a
multi-sign-off workflow reports the running tally). It is the click-through
target for the dashboard's pending-approval badges, so you can clear the
overnight backlog without tabbing through each sandbox.

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

## Phone approvals

For overnight or away-from-keyboard runs, you can approve from your phone. The
**Set up phone approvals** wizard (`/setup/phone-approvals`, linked from your
profile) does it in one click: it subscribes the device to web push, wires a
`webpush` webhook to the approval events, and fires a sample notification to
tap. In `--local` mode the prerequisites — one-tap approve/reject links
(`SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS`) and a reachable `public_url` derived
from the LAN/Tailscale bind — are on by default, so a `approval.pending`
notification carries a button that casts a single vote from a mobile
confirmation page. The phone needs to reach ShoreGuard over a secure context
(localhost or HTTPS, e.g. `tailscale serve`); see
[Tailscale remote access](../operations/tailscale.md). One-tap links and the
Telegram/ntfy channels are described in the [webhooks guide](webhooks.md).

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
