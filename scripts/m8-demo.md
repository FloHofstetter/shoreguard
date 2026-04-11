# M8 — Multi-Gateway Federation Runbook

The canonical recipe for the M8 federation demo: two real OpenShell
clusters, two labels, one ShoreGuard, one operator.

## What this proves

ShoreGuard is **federation-aware** out of the box: a single
admin/operator can register multiple OpenShell clusters under one
control plane, slice the view by labels (`env=dev`, `env=staging`,
…), and trust that every audit row, every approval, and every routed
inference call lands on the right cluster with correct attribution.
None of the federation surfaces share state across gateways
accidentally.

The M7 milestone proved one cluster end-to-end. M8 proves the same
flow runs **independently and concurrently** on two clusters, and
that the cross-cutting surfaces (audit attribution, label filter,
gateway list, ⌘K search, topbar switcher) keep them separate.

If any phase fails, that's the M8 gap to fix.

---

## Prerequisites

- OpenShell binary on PATH (currently the latest stable, v0.0.26).
- Real Anthropic API key in `ANTHROPIC_API_KEY`.
- ShoreGuard repo, `uv sync` clean.
- Two free TCP ports for the gateway clusters (default `8089` + `8189`).
- One free TCP port for ShoreGuard itself (default `8888`).

## Phase 0 — Bring up the federation stack

```bash
# Terminal 1+2 — bring up two OpenShell gateways:
openshell gateway start --name cluster-dev --port 8089 --plaintext --disable-gateway-auth
openshell gateway start --name cluster-staging --port 8189 --plaintext --disable-gateway-auth

# Terminal 3 — ShoreGuard with a fresh DB:
export SHOREGUARD_DATABASE_URL=sqlite:////tmp/sg-m8.db
export SHOREGUARD_LOCAL_MODE=true
export SHOREGUARD_ALLOW_UNSAFE_CONFIG=true
export SHOREGUARD_ADMIN_PASSWORD='m8-demo-pass'  # pragma: allowlist secret
export ANTHROPIC_API_KEY='sk-ant-...'  # pragma: allowlist secret
uv run uvicorn shoreguard.api.main:app --host 127.0.0.1 --port 8888
```

Health check both:

```bash
curl -s http://127.0.0.1:8888/healthz
curl -s http://127.0.0.1:8888/version
```

## Phase A — Login

Open <http://127.0.0.1:8888/login>, sign in as `admin@localhost` with
the value of `SHOREGUARD_ADMIN_PASSWORD`. You should land on
`/dashboard` and see **2 gateways / 2 connected** in the Gateways
stat card. The "Gateway Status" sidebar lists both
`cluster-dev` and `cluster-staging` as clickable rows.

## Phase B+C — Register both gateways with labels

> **Local-mode gotcha:** ShoreGuard auto-registers both clusters at
> startup with the wrong `auth_mode` (cert material on a plaintext
> gateway → `unreachable`). Delete each before re-registering:

In the gateways table at `/gateways`, click **Unregister** on each
row, then click **Register** twice and fill in:

| Field        | cluster-dev          | cluster-staging          |
|--------------|----------------------|--------------------------|
| Name         | `cluster-dev`        | `cluster-staging`        |
| Endpoint     | `127.0.0.1:8089`     | `127.0.0.1:8189`         |
| Scheme       | `http`               | `http`                   |
| Auth Mode    | `Insecure`           | `Insecure`               |
| Description  | M8 dev cluster       | M8 staging cluster       |
| Labels       | `env` = `dev`        | `env` = `staging`        |

Both should land at status `connected`.

## Phase D — Label filter assertion

On `/gateways`, in the second filter input ("Label filter (e.g.
env:dev)"), type `env:dev`. The table should reduce to one row:
`cluster-dev`. Type `env:staging` instead → only `cluster-staging`.
Clear → both rows back. This is the read path of the new
[label filter UI](../frontend/templates/pages/gateways.html) +
[backend `?label=` query param](../shoreguard/api/routes/gateway.py).

## Phase E — Provider + inference + sandbox on each gateway

Use the topbar **Switch gateway** dropdown to land on `cluster-dev`,
then walk:

1. Open the **Inference Configuration** section, pick provider type
   `Anthropic`, model `claude-sonnet-4-5-20250929`, save.
2. Click **New Sandbox**, pick the `base` template, name it
   `m8-base`, attach the `anthropic-demo` provider, launch.

Repeat for `cluster-staging` (use the topbar switcher again).
**Both** sandboxes should reach `phase=ready`.

## Phase F — Routed inference on both sandboxes

In two terminals (or one, sequentially):

```bash
openshell --gateway-endpoint http://127.0.0.1:8089 sandbox exec \
    --name m8-base -- claude -p 'Reply with PONG and nothing else.'

openshell --gateway-endpoint http://127.0.0.1:8189 sandbox exec \
    --name m8-base -- claude -p 'Reply with PONG and nothing else.'
```

Both should return `PONG`.

## Phase G — L7 denial + approve + retry on each gateway

Use a **different** unallowlisted host per gateway so the audit
attribution is unambiguous:

```bash
# cluster-dev → httpbin.org
openshell --gateway-endpoint http://127.0.0.1:8089 sandbox exec \
    --name m8-base -- curl -4 -sI https://httpbin.org/

# cluster-staging → jsonplaceholder.typicode.com
openshell --gateway-endpoint http://127.0.0.1:8189 sandbox exec \
    --name m8-base -- curl -4 -sI https://jsonplaceholder.typicode.com/
```

Both should return `HTTP/1.1 403 Forbidden` from the proxy CONNECT.

In the UI, switch to each sandbox's `/approvals` page, approve the
draft chunk that appeared. **Wait for the proxy to reload** — poll
the policy endpoint for `revision.status="loaded"` matching the new
`active_version` (the M8 demo script does this automatically). Then
retry the curl call: it should return HTTP 200 with body bytes.

## Phase H — Federation assertion: per-gateway audit attribution

Navigate to `/audit`. In the Gateway filter input, type `cluster-dev`.
You should see only rows where the Gateway column is `cluster-dev`:
the register, the provider create, the inference update, the sandbox
create, the approval. Switch the filter to `cluster-staging`: same
shape, but every row is for `cluster-staging`.

**Critical assertion:** *no row from one gateway appears under the
other gateway's filter.* If it does, the M8 closeout is not done.

## Phase I — Federation assertion: unfiltered audit shows both

Clear the gateway filter. The audit log should now show entries from
both gateways interleaved chronologically, with the Gateway column
populated correctly per row, plus a few global rows (like
`user.login` and `user.login_failed`) that have no gateway.

## Phase J — Federation assertion: gateway list with labels

`GET /api/gateway/list` (curl or DevTools) should return both with
their labels intact and `status=connected`. This is what the topbar
switcher dropdown reads to render the federation menu.

## Phase K — Topbar switcher + ⌘K search visual sanity

- Hit **⌘K** anywhere in the UI. Both `cluster-dev` and
  `cluster-staging` should appear under the **Gateways** group of the
  search palette.
- Click the topbar **Switch gateway** dropdown. Both should appear
  with status dots and label tail (`env=dev` / `env=staging`).
  Clicking either navigates to that gateway's detail page; the
  topbar button updates to show the active gateway name.

## Reporting back

| Phase | Worked? | Gap | Notes |
|-------|---------|-----|-------|
| 0     |         |     |       |
| A     |         |     |       |
| B+C   |         |     |       |
| D     |         |     |       |
| E     |         |     |       |
| F     |         |     |       |
| G     |         |     |       |
| H     |         |     |       |
| I     |         |     |       |
| J     |         |     |       |
| K     |         |     |       |

If everything passes, you don't need this runbook again — run
`scripts/m8_demo.py` instead, which automates phases A through J in
~3-4 min.

## Automation

```bash
SHOREGUARD_ADMIN_PASSWORD='...' ANTHROPIC_API_KEY='sk-ant-...' \  # pragma: allowlist secret
    uv run python scripts/m8_demo.py
```

Same prerequisites as Phase 0. The script is idempotent — it deletes
any leftover gateway/provider/sandbox records before recreating them,
so you can re-run it as often as you like against the same stack.
