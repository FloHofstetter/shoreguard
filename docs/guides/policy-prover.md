# Policy Prover

The Policy Prover runs Z3-backed formal verification against the
active policy of a sandbox. Unlike the visual editor's "diff" view,
which shows what changed, the Prover answers questions about what
the current policy *allows* — before an agent exercises it.

## What it solves

Policy files are composed of network rules, filesystem paths, process
settings, and presets. Interactions between them are hard to keep in
your head: a readonly root that gets overridden by a writable bind
mount, an allow-list that becomes permissive through a wildcard port
range, a binary whose hash is allowed but whose ancestry is not.
The Prover encodes the policy as Z3 constraints and asks whether any
assignment of concrete values satisfies a dangerous property. If
yes, it returns the witness model so you can see *why*.

## Available templates

Four built-in query templates ship with v0.30.2:

| Template | Question |
|---|---|
| `can_exfiltrate` | Is there a writable egress path to a non-whitelisted destination? |
| `unrestricted_egress` | Does any network rule allow `0.0.0.0/0` on an unbounded port range? |
| `binary_bypass` | Can a binary hash outside the allowlist be executed? |
| `write_despite_readonly` | Can any filesystem write succeed despite a readonly root? |

Each returns **SAT** (a witness exists → the property fails) or
**UNSAT** (the property holds for every possible assignment).

## Using the Verify tab

On the sandbox detail page, open **Verify**, pick a template from the
preset picker, and hit **Run**. On SAT the UI renders the witness
model as a table (binary, destination, port, filesystem path — only
the fields relevant to the template). On UNSAT you get a green
"property holds" banner.

## Using the API

```http
POST /api/gateways/dev/sandboxes/agent-a/policy/verify
Content-Type: application/json
Authorization: Bearer $SHOREGUARD_TOKEN

{"template": "can_exfiltrate", "params": {}}
```

List templates:

```
GET /api/gateways/dev/policies/presets/verify
```

See [API reference](../reference/api.md#policy-prover-m17-v0302).

## Limits

Z3 timeouts are configurable per query (default 5 s). Complex
policies with thousands of rules may require higher limits. The
solver does not model runtime state — it reasons about the policy
as written, not the agents that run against it.
