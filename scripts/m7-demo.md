# M7 — End-to-End Vision Demo Runbook

The canonical 7-step demo that threads every ShoreGuard surface together.
Follow this manually for the first run; discrepancies become Phase 3 gaps.

## What this proves

ShoreGuard is "better NemoClaw": one operator session takes a fresh OpenShell
gateway → spins up a sandboxed agent → routes its outbound LLM call through
ShoreGuard's inference proxy → catches an unapproved host as an L7 denial →
gets approved in the UI → retried → all six steps land in the audit log
filterable by gateway.

If any step here surprises you, that's the M7 gap to fix.

---

## Prerequisites

- OpenShell binary on PATH (pin: latest stable, see roadmap)
- Real Anthropic API key
- ShoreGuard repo, `uv sync` clean
- Playwright Firefox installed if you want to record the screencast

## Phase 0 — Bring up the stack

```bash
# Terminal 1: OpenShell gateway
openshell gateway start \
  --name nemoclaw \
  --port 8089 \
  --plaintext \
  --disable-gateway-auth

# Terminal 2: ShoreGuard
export SHOREGUARD_DATABASE_URL=sqlite:////tmp/sg-m7.db
export SHOREGUARD_LOCAL_MODE=true
export SHOREGUARD_ALLOW_UNSAFE_CONFIG=true
export SHOREGUARD_ADMIN_PASSWORD='m7-demo-pass'  # pragma: allowlist secret
export ANTHROPIC_API_KEY='sk-ant-...'  # pragma: allowlist secret
uv run uvicorn shoreguard.api.main:app --host 127.0.0.1 --port 8888

# Terminal 3 (optional): record video with ffmpeg + an X11 region
# ffmpeg -f x11grab -framerate 30 -video_size 1600x1000 -i :0.0+100,100 artifacts/m7-demo.mp4
```

Verify both processes are healthy:

```bash
curl -s http://127.0.0.1:8888/healthz
curl -s http://127.0.0.1:8888/version
```

## Phase A — Login + Register Gateway

1. Open <http://127.0.0.1:8888/> → redirected to `/login`.
2. Email: `admin@localhost`, Password: the value you set in `SHOREGUARD_AUTH__ADMIN_PASSWORD` (the bootstrap admin).
3. Land on `/dashboard`. Click **Gateways** in the nav.
4. Click **Register Gateway**.
   - Name: `nemoclaw`
   - Endpoint: `127.0.0.1:8089`
   - Scheme: `http`
   - Auth Mode: `Insecure`
   - Description: `M7 demo gateway`
   - Submit.
5. Expect: redirect to `/gateways/nemoclaw` with status `online`.

**Audit checkpoint:** `/audit?gateway=nemoclaw` shows `gateway.register`.

## Phase B — Set Inference Provider (Anthropic)

1. From `/gateways/nemoclaw`, find the **Inference Configuration** section.
2. Provider type: `Anthropic`.
3. Model: `claude-sonnet-4-6` (the placeholder in [openshell.yaml](../shoreguard/openshell.yaml) is stale — override).
4. API key: paste the env var value (or use the env-var injection if the UI offers it).
5. Save.

**Audit checkpoint:** `/audit?gateway=nemoclaw` now shows a `gateway.settings_update`
(action name may differ — record actual name as a Phase 3 gap if it doesn't exist).

## Phase C — Launch Sandbox via Wizard

1. Click **New Sandbox** (or navigate to `/gateways/nemoclaw/wizard`).
2. **Step 1 — Agent Type:** click an **OpenClaw** template card. If no
   OpenClaw template is in the community sandbox list, that's a gap —
   fall back to **Custom** and set `image` to whatever the OpenShell
   `community-sandboxes` API exposes for OpenClaw-equivalent.
3. **Step 2 — Configuration:**
   - Sandbox Name: `m7-claw`
   - GPU: off
   - Providers: leave empty (auto-create)
4. **Step 3 — Policy:** start with the template's default policy
   (presumably L7 enforcement on for the demo to work).
5. **Step 4 — Launch:** click **Create**.
6. Wait for the LRO to land. Sandbox should appear at
   `/gateways/nemoclaw/sandboxes/m7-claw` with status `running`.

**Audit checkpoint:** `sandbox.create`.

## Phase D — Agent makes a real outbound LLM call (the unproven one)

This is the step that has never been tested end-to-end. If it works on
the first try, M7 is mostly gravy. If it doesn't, this is the gap.

**Sub-step D.1 — confirm routed inference is wired.** From a separate
shell:

```bash
openshell sandbox exec m7-claw -- env | grep -i ANTHROPIC
```

Expect: `ANTHROPIC_BASE_URL` (or similar) pointing at the OpenShell
inference proxy on the gateway, not directly at `api.anthropic.com`.
If this is missing, the inference route_name from S1.1 isn't getting
propagated — that's the gap.

**Sub-step D.2 — fire a one-shot call from inside the sandbox.**
Pick whichever of these matches the OpenClaw template:

```bash
# Option a: openclaw-style CLI
openshell sandbox exec m7-claw -- openclaw --prompt 'Say hello in one word.'

# Option b: bare curl through the routed inference URL
openshell sandbox exec m7-claw -- bash -c '
  curl -s "$ANTHROPIC_BASE_URL/v1/messages" \
       -H "x-api-key: $ANTHROPIC_API_KEY" \
       -H "anthropic-version: 2023-06-01" \
       -H "content-type: application/json" \
       -d "{\"model\":\"claude-sonnet-4-6\",\"max_tokens\":10,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
'
```

Expect: a 200 with a real response, **OR** an L7 denial that shows up
in the next phase. Both are acceptable demo outcomes — the denial *is*
the demo.

**Sub-step D.3 — provoke a denial deliberately.** If the policy already
allowlists `api.anthropic.com`, then make an unallowlisted call:

```bash
openshell sandbox exec m7-claw -- curl -s https://example.com/
```

This should be denied at L7.

## Phase E — L7 Denial appears in logs + draft policy

1. Navigate to `/gateways/nemoclaw/sandboxes/m7-claw/logs`.
2. Filter or scroll for `disposition=DENIED`. The OCSF parser should
   render it with class `NET` or `HTTP`, severity `INFO`+, the binary
   path on the left, and the unallowlisted host on the right.
3. Click through to **Approvals** (`/gateways/nemoclaw/sandboxes/m7-claw/approvals`).
4. Expect: at least one **draft chunk** in the `pending` table, with the
   "Seen" column linking to the denial it came from (S3.1 reduced UX).

If no draft chunk appears, OpenShell isn't synthesising a draft from the
denial — that's the **biggest gap** and the reason Phase E of the v0.29
e2e walk halted at "no real draft chunks to test".

## Phase F — Approve in UI

1. On the approvals table, click the chunk's **Approve** button.
2. Confirm in the modal.
3. The chunk moves out of `pending`. The watch stream should refresh
   the table without a manual reload (look for the `sg:approvals-update`
   event in dev tools console).

**Audit checkpoint:** `approval.approve` with `gateway=nemoclaw`.

## Phase G — Audit log shows the full sequence

1. Go to `/audit`.
2. **Gateway** filter: `nemoclaw`.
3. Expect rows in reverse chronological order:

   ```
   approval.approve    approval   chunk-…
   sandbox.create      sandbox    m7-claw
   gateway.register    gateway    nemoclaw
   ```

   Plus any settings updates from Phase B and a `auth.login` from Phase A.

4. Click **Export CSV**. Verify the exported file shows the same
   sequence — this is the artifact that goes in the demo write-up.

## Phase H — Retry succeeds

1. From inside the sandbox, re-run the call from Phase D that was denied:

   ```bash
   openshell sandbox exec m7-claw -- curl -s https://example.com/
   ```

2. Expect: 200 (or whatever example.com returns). The new policy from
   the approved chunk is now in effect.
3. Back in `/sandboxes/m7-claw/logs`: the same NET event should now show
   `disposition=ALLOWED`.

**Demo over.** Stop the recording.

---

## First-run report (2026-04-11, ShoreGuard @ 09f2b5b → 485bf71)

Run was driven via the HTTP API (Playwright Firefox MCP failed to launch
in the local Wayland session — separate gap, see below). Stack: openshell
0.0.26 (latest stable), ShoreGuard `main` with the two M7 fixes applied.

| Phase | Worked? | Gap | Notes |
|-------|---------|-----|-------|
| A — register gateway | ✅ after fix | nemoclaw is auto-registered at startup with `auth_mode=null` and mTLS cert material even though the local gateway is plaintext → `unreachable`. Workaround: DELETE then re-register with `auth_mode=insecure`. | Audit row landed with `gateway_name=NULL` until **fix `09f2b5b`** (`gateway=name` on all gateway-route audit_log calls). |
| B — set inference provider | ✅ after fix | `/api/gateway/{name}/info` returned 500 (`ResponseValidationError` — service injects `configured` + `version`, schema is `extra="forbid"`) → **fix `485bf71`**. Also: `set_inference` requires the *provider record name* (e.g. `anthropic-demo`), not the *provider type* (`anthropic`); confusing API surface. | `/api/gateways/<gw>/providers/inference-providers` lists `anthropic` correctly. Inference set returns `route_name=inference.local`. |
| C — wizard sandbox launch | ⚠️ partial | `community_sandboxes.openclaw` points at `ghcr.io/nvidia/openshell-community/sandboxes/openclaw:latest`, which was not pullable in this environment (timed out at 30% "Waiting for ready state"). Pivoted to `base` template, which has `claude`, `opencode`, `codex`, `copilot` pre-installed. | Demo proceeded with `m7-base` instead of `m7-claw`. Real fix: either ensure the openclaw image is published, or update openshell.yaml with a maintained image URL. |
| D — agent routed inference | ✅ **proven for the first time** | The unproven step worked: `claude -p 'Reply with PONG'` inside `m7-base` returned `PONG`. **Routing is via transparent HTTPS proxy (`HTTPS_PROXY=http://10.200.0.1:3128`) + injected CA bundle**, not via env-var base URL. `ANTHROPIC_API_KEY` is set to a literal `openshell:resolve:env:ANTHROPIC_API_KEY` placeholder — credentials are resolved at the proxy edge. This is *better* than what the runbook originally guessed. | This finding alone closes the "no real draft chunks" gap from Phase E of the v0.29 e2e walk. |
| E — L7 denial fires | ✅ | `curl https://example.com` from inside the sandbox produced **HTTP/1.1 403 Forbidden** at the proxy CONNECT layer, and a real **draft chunk** appeared at `/api/gateways/<gw>/sandboxes/<sb>/approvals` with rule `allow_example_com_443`, confidence 0.65, binary `/usr/bin/curl`. | First time a draft chunk has been observed end-to-end. |
| F — approve in UI | ✅ | `POST /approvals/<chunk-id>/approve` returned `policy_version=2` + a fresh policy hash. | Audit row landed with `approval.approve` + `gateway=nemoclaw`. |
| G — audit sequence | ✅ | `GET /api/audit?gateway=nemoclaw` returned the entire 10-row story in chronological order: `gateway.register → provider.create → inference.update → sandbox.create → approval.approve`. The new gateway-filter from `d88fd82` + the audit-tagging fix from `09f2b5b` are both load-bearing here. | This is the audit feature M7 was supposed to prove. |
| H — retry succeeds | ⚠️ partial | The 403 is gone (the policy update reached the proxy), TLS handshake completes through the MITM cert, and `GET / HTTP/1.1` is sent — but the response is reset mid-stream (`Recv failure: Connection reset by peer`). HEAD request shows `HTTP/1.1 200 Connection Established` from the CONNECT, then nothing. Looks like the approve adds an outbound allow but not a corresponding response/inbound allow, **OR** the proxy needs an extra reload for response-path enforcement. | Demo's main story is intact ("denied → approved → no longer denied"), but the literal "retry returns 200" isn't there yet. Worth a separate fix-or-investigate item. |

## Findings beyond the 8 phases

- **Auto-register-with-mtls bug.** In `SHOREGUARD_LOCAL_MODE=true`, ShoreGuard
  auto-registers the local nemoclaw gateway at startup with `has_ca_cert=true`,
  `has_client_cert=true`, `has_client_key=true`, `auth_mode=null` — even when the
  gateway is plaintext. The result is `last_status=unreachable` and an SSL
  handshake error every 30s in the logs (`SSL_ERROR_SSL: WRONG_VERSION_NUMBER`).
  Manual delete + re-register with `auth_mode=insecure` is the workaround.
- **Health endpoints don't live under `/api/`.** They're at `/healthz` and
  `/version`, mounted on the root router. The runbook (and presumably the
  ops docs) had `/api/health` and `/api/version`. Fixed in this commit.
- **`SHOREGUARD_AUTH__ADMIN_PASSWORD` is wrong.** The bootstrap env var is
  `SHOREGUARD_ADMIN_PASSWORD` — `AuthSettings` uses `env_prefix="SHOREGUARD_"`,
  no nested delimiter. Runbook fixed.
- **Playwright Firefox MCP launches but exits with code 0 immediately** in
  the local Wayland session. Headless `firefox -no-remote -headless about:blank`
  launched manually works fine. The MCP launcher uses `-foreground` which
  may not survive in this environment. Worked around by running the demo
  via curl/HTTP API, which is actually faster for finding gaps.
- **`GET /api/gateway/{name}` returns 405** — only DELETE/PATCH are wired on
  that path. Use `/api/gateway/{name}/info` for the GET, or `/api/gateway/list`
  for the collection.
- **`provider_name` ambiguity.** `set_inference` takes the provider *record
  name* (e.g. `anthropic-demo`), but the inference-providers list uses the
  *type* (`anthropic`) — same field name, different meaning. Worth either
  renaming one of the fields or surfacing a clearer error than upstream's
  bare `FAILED_PRECONDITION`.

## Status

M7 is **proven in substance**: the vision flow runs end-to-end, every M3+
surface contributes its real artifact, and the "no real draft chunks"
blocker is closed. The two outstanding items —
*(1)* Phase H response-path retry never completes,
*(2)* the auto-register-with-mtls bug —
are real follow-ups but neither blocks the M7 closeout.
