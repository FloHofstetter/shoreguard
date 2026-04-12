# Migrating from NemoClaw

ShoreGuard is an open-source alternative to NVIDIA NemoClaw with a
broader scope. This guide is for teams already running NemoClaw who
are evaluating a switch.

## Executive summary

NemoClaw is a CLI + blueprint-profile system that orchestrates
OpenShell against a **single gateway**. ShoreGuard is a web UI +
REST API + Terraform provider + CLI that orchestrates OpenShell
against **many gateways**, with a governance layer on top — quorum
approvals, formal verification, GitOps sync, bypass detection,
SBOM — that NemoClaw does not have.

If your team already runs NemoClaw happily and fits on one gateway
with one operator, you do not need to migrate. The NemoClaw CLI is
perfectly fine for that shape.

If you need *any* of the following, ShoreGuard is the better fit:

- More than one OpenShell gateway
- More than one operator (with RBAC and audit trail)
- Approval changes that must route through quorum / on-call
- Policy verified before a change ships (Z3 prover)
- Policy expressed as code in Git (GitOps) and enforced by CI
- SBOM visibility per sandbox
- Webhooks into Slack / Discord / Email
- Terraform as the provisioning surface

## Concept mapping

| NemoClaw | ShoreGuard | Notes |
|---|---|---|
| `nemoclaw` CLI | ShoreGuard Web UI + [shoreguard CLI](../reference/cli.md) | Both are thin wrappers over OpenShell RPCs. ShoreGuard adds a REST API in between. |
| Blueprint profile | [Sandbox template](../guides/sandboxes.md#sandbox-templates) | YAML bundles image + GPU + providers + preset. Built-ins: `data-science`, `web-dev`, `secure-coding`. |
| Single gateway | [Multi-gateway](../guides/gateways.md) | One ShoreGuard manages many gateways, per-gateway labels and health monitoring. |
| CLI-driven approvals | [Approval flow](../guides/approvals.md) + [Approval Workflows](../guides/approval-workflows.md) | Real-time UI + optional quorum + webhooks. |
| Policy lives in blueprint YAML | [GitOps policy sync](../guides/gitops.md) | `shoreguard policy export` / `diff` / `apply`, optimistic locking, drift detection. |
| Credentials baked into profile | [Inference routing](../guides/gateways.md#inference-provider) | Agents see `inference.local`, never real keys. ShoreGuard owns the credentials. |
| Single operator | [RBAC + audit](../admin/rbac.md) | Admin / Operator / Viewer with gateway-scoped overrides + persistent audit log. |
| (none) | [Policy Prover](../guides/policy-prover.md) | Z3 formal verification with built-in templates. |
| (none) | [Bypass Detection](../guides/bypass-detection.md) | OCSF classification with MITRE ATT&CK mapping. |
| (none) | [SBOM Viewer](../guides/sbom.md) | CycloneDX ingestion + offline CVE browse. |
| (none) | [Boot Hooks](../guides/boot-hooks.md) | Pre/post-create validation + warm-up. |
| (none) | [Gateway Discovery](../guides/gateway-discovery.md) | DNS SRV auto-registration. |
| (none) | [Webhooks](../guides/webhooks.md) | Slack / Discord / Email / HMAC-signed generic. |
| (none) | [Terraform provider](https://github.com/FloHofstetter/terraform-provider-shoreguard) | Gateways, RBAC, workflows, pins, boot hooks as code. |

## Migration steps

### 1. Keep OpenShell in place

You do not need to re-deploy your OpenShell gateways. ShoreGuard
talks to the same gRPC + mTLS surface NemoClaw does — **v0.0.26** is
the pinned upstream version. If your cluster is older, see the
[OpenShell upgrade guide](https://github.com/NVIDIA/OpenShell).

### 2. Install ShoreGuard alongside NemoClaw

ShoreGuard has no stake in where it runs — pick whichever fits:

```bash
# Local / homelab / single VM
pip install shoreguard
shoreguard --local --no-auth

# Docker Compose (single VM with HTTPS)
cd deploy/
docker compose up -d

# Kubernetes (production)
helm install shoreguard charts/shoreguard -f values.production.yaml
```

See the [installation guide](../getting-started/installation.md).

### 3. Register your existing gateway

From the ShoreGuard UI: **Gateways** → **+ Register**. Paste the same
gRPC endpoint, mTLS certs, and auth mode your NemoClaw uses. Or:

```bash
curl -X POST \
  -H "Authorization: Bearer $SHOREGUARD_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"dev","endpoint":"grpcs://...","mtls":{...}}' \
  "$SHOREGUARD_URL/api/gateway/register"
```

If you have many gateways, enable [DNS SRV discovery](../guides/gateway-discovery.md).

### 4. Import your blueprint profiles as templates

NemoClaw blueprints have no automatic importer (the formats are
different enough that a naive converter would be wrong more often
than right). Re-create each profile as a
[sandbox template](../guides/sandboxes.md#sandbox-templates), or
create the sandbox once from the wizard and then export its policy:

```bash
shoreguard policy export --gateway dev --sandbox agent-a > agent-a.yaml
```

Check the YAML into Git — that is now your source of truth.

### 5. Replace CLI approval with the Web UI (or quorum)

Route your team to the ShoreGuard UI for approvals. If your change
management rules require multiple sign-offs, configure an
[Approval Workflow](../guides/approval-workflows.md) for each
production sandbox with `required_votes: 2` and a reasonable
escalation deadline.

### 6. Wire CI into GitOps

Add a `shoreguard policy diff` job to your PRs and a
`shoreguard policy apply` job on the default branch — see the
[GitOps guide](../guides/gitops.md) for a sample GitHub Actions
workflow. Under an active approval workflow the CI apply records
one vote, and the second human voter completes quorum from the UI.

### 7. Retire NemoClaw

Once the team is comfortable using ShoreGuard for approvals and CI
owns the policy YAMLs, the NemoClaw CLI has no remaining job. You
can uninstall it; your OpenShell gateways keep running unchanged.

## What you cannot bring over

- **Profile inheritance chains.** NemoClaw's blueprint inheritance has
  no direct equivalent. The closest analogue is templates + presets,
  composed in the YAML.
- **Custom NemoClaw plugins.** The plugin ABI is different.
  Rewrite them as ShoreGuard webhooks, or as a thin REST client.

## Questions worth asking before you migrate

- *Do you actually have more than one gateway today?* If no, and
  you never will, NemoClaw's CLI is lower overhead.
- *Is CI the source of truth for policy, or the UI?* ShoreGuard
  supports both, but the GitOps path is significantly more
  valuable when CI owns policy — that is where the pinning,
  quorum, and drift-detection features start paying for themselves.
- *Do you need an audit trail that survives operator departures?*
  If yes, NemoClaw's CLI is not a good fit — everything it does is
  ephemeral. ShoreGuard's audit log is.
