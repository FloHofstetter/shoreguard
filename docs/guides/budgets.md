# Usage metering & budgets

The "$47 echo loop" guardrail: ShoreGuard can meter each sandbox's
inference traffic and stop a runaway agent before it drains your API
budget — or your GPU.

## Enable metering

```bash
SHOREGUARD_BUDGET_METERING_ENABLED=true
```

The `usage_metering` background task polls each running sandbox's
gateway logs (cursor-based, every `SHOREGUARD_BUDGET_INTERVAL_SECONDS`,
default 60 s) and counts inference-proxy lines. Counters are per
sandbox per UTC day; the sandbox detail page shows today's count, and
`GET /api/usage/summary` lists the top consumers across all gateways.

!!! note "Phase 1 — log-derived approximation"
    OpenShell's gRPC surface has no usage RPC yet, so a "request" is an
    inference-proxy **log line**, not a token count. Which lines count
    is configurable (`SHOREGUARD_BUDGET_INFERENCE_SOURCES`, default
    `inference,proxy`, plus any line whose target mentions
    `inference`). Metering starts at activation — history is never
    billed. When upstream ships a metering RPC, it replaces this.

## Set a budget

On the sandbox detail page (**Inference usage & budget**), or via API:

```http
PUT /api/gateways/{gw}/sandboxes/{name}/budget
Content-Type: application/json

{"limit_requests": 1000, "window": "daily", "action": "notify"}
```

- `window`: `daily`, `weekly`, `monthly`, or `total`.
- `action` at the limit:
    - `notify` — fire a `budget.exceeded` webhook (once per window).
      Pair it with an [ntfy or Telegram channel](webhooks.md) to get it
      on your phone.
    - `detach` — **cut the sandbox's providers**: the agent keeps its
      state but loses inference and tool credentials instantly. The
      detached set is recorded as a kill-switch entry
      (`engaged_by: budget`), so the gateway detail page shows it and
      **Resume providers** re-attaches everything once you've decided
      the agent may continue.

Either way the event carries `used`, `limit`, and `window`, and the
decision is yours — budgets never delete sandboxes.
