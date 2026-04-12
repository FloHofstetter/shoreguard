# Sandbox Boot Hooks

Boot Hooks let operators attach pre- and post-create steps to a
sandbox. Pre-create hooks run in the ShoreGuard process *before*
`CreateSandbox` reaches the gateway; post-create hooks run *inside*
the new sandbox immediately after creation.

## What they solve

Two recurring patterns have no clean home in the base sandbox model:

1. **Validation gates** that must block creation (check a CMDB, call
   an external approval API, verify quota). These need to run before
   the gateway ever sees the request.
2. **Warm-up tasks** that every freshly-minted sandbox needs
   (`apt update`, telemetry init, SSH key import). These need to
   run inside the sandbox, once, and surface any failures.

Hooks make both first-class and attach them to the sandbox record
so they outlive ad-hoc shell scripts.

## Pre-create hooks

Pre-create hooks execute in the ShoreGuard process via
`subprocess.run` with a **whitelisted environment**:

- `SG_SANDBOX_NAME` — the sandbox being created
- `SG_SANDBOX_IMAGE` — the image reference
- `SG_SANDBOX_POLICY_ID` — the policy ID attached at create time
- plus any user-defined `env` entries from the hook config

A non-zero exit raises `BootHookError` and aborts the create. If
`continue_on_failure: true`, the failure is logged + surfaced in the
response but creation continues.

## Post-create hooks

Post-create hooks run *inside the new sandbox* via the existing
`ExecSandbox` RPC once `CreateSandbox` succeeds. Their exit code is
captured, stdout/stderr is truncated to 4 KiB, and the result is
written to the hook record (`last_status`, `last_output`).

Failures from `continue_on_failure: true` post-create hooks are
surfaced in the sandbox creation response under
`boot_hooks.post_create` but do **not** roll back the sandbox — the
rationale is that warm-up failures are almost always recoverable
from inside the live sandbox, and rolling back leaks half-created
state.

## Hook configuration fields

| Field | Description |
|---|---|
| `phase` | `pre_create` or `post_create` |
| `command` | Shell command or argv |
| `workdir` | Working directory (post-create only) |
| `env` | KEY=VALUE dict (pre-create: merged into whitelisted env) |
| `timeout_seconds` | Per-hook timeout |
| `order` | Execution order within the phase (ascending) |
| `enabled` | Toggle without delete |
| `continue_on_failure` | See above |

## Managing hooks

From the UI: sandbox detail page → **Hooks** tab. Separate
Pre-create / Post-create sections, in-place toggle, reorder buttons,
editor modal, one-click **Run** for manual trigger.

From the API:

```http
POST /api/gateways/dev/sandboxes/agent-a/hooks
Content-Type: application/json

{
  "phase": "post_create",
  "command": "apt update && apt install -y htop",
  "timeout_seconds": 60,
  "enabled": true
}
```

From Terraform (M24):

```hcl
resource "shoreguard_sandbox_boot_hook" "warmup" {
  gateway      = "dev"
  sandbox_name = "agent-a"
  phase        = "post_create"
  command      = "apt update && apt install -y htop"
  timeout_seconds = 60
}
```

## Recovery: `skip_hooks`

The `POST .../sandboxes` endpoint accepts an admin-only
`skip_hooks: true` flag that bypasses both phases. Use it when a
broken pre-create hook is blocking all new sandboxes and you need to
create one to diagnose the issue.

## Reference

- API: [`/api/gateways/{gw}/sandboxes/{name}/hooks`](../reference/api.md#boot-hooks-m22-v0302)
- Audit events: `boot_hook.created`, `boot_hook.updated`,
  `boot_hook.deleted`, `boot_hook.reordered`, `boot_hook.manual_run`.
- Demo: `scripts/m22_demo.py` phases 1–4.
