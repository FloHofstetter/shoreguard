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
