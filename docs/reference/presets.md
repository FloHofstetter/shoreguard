# Policy Presets

ShoreGuard ships with bundled YAML templates — called **presets** — for common
endpoint groups. Each preset opens the minimal set of domains and ports a
sandbox needs to reach a specific service.

## Available presets

| Preset | Description |
|--------|-------------|
| `pypi` | Python Package Index (`pypi.org`, `files.pythonhosted.org`) |
| `npm` | npm and Yarn registries |
| `docker` | Docker Hub and NVIDIA Container Registry |
| `huggingface` | Hugging Face Hub, LFS, and Inference API |
| `slack` | Slack API and webhooks |
| `discord` | Discord API, gateway, and CDN |
| `telegram` | Telegram Bot API |
| `jira` | Jira / Atlassian Cloud |
| `outlook` | Microsoft Graph / Outlook |

## Applying presets

### From the policy editor

Open a sandbox's policy tab and click **Add Preset**. The preset rules are
merged into the existing policy.

### From the sandbox wizard

During sandbox creation the wizard offers a preset picker. Selected presets are
applied when the sandbox is created.

### Via the API

```
POST /api/gateways/{gw}/sandboxes/{name}/policy/presets/{preset}
```

Replace `{preset}` with one of the names listed above (e.g. `pypi`).

## Storage

Presets are local YAML files bundled with ShoreGuard. They are not
gateway-scoped and are the same across all gateways in a deployment.
