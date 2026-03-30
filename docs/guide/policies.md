# Security Policies

## What is a policy?

Every sandbox has a **security policy** that controls what the agent is allowed
to do. A policy consists of three sections:

- **Network rules** — which endpoints the agent can reach (and how).
- **Filesystem paths** — which directories are readable or writable.
- **Process settings** — run-as user/group and landlock compatibility level.

ShoreGuard replaces the export-edit-import YAML cycle with a visual editor
that lets you modify all three sections directly in the browser.

![Policy](../screenshots/policy.png)

## Visual policy editor

The editor is divided into tabs — one for each policy section. You can add,
edit, and remove rules without touching any YAML. Changes are validated
client-side before being sent to the gateway.

## Network rules

![Network Policies](../screenshots/network-policies.png)

Network rules are organized into **endpoint groups**. Each group contains one
or more rules that specify:

| Field | Description |
|-------|-------------|
| **Endpoint** | Host and port (e.g., `api.github.com:443`) |
| **Method** | HTTP method (`GET`, `POST`, `*`, etc.) |
| **Path** | URL path pattern (e.g., `/repos/*`) |
| **Action** | `allow` or `deny` |

Rules are evaluated top-to-bottom within a group. The first matching rule wins.

## Filesystem paths

Each entry grants the agent access to a directory inside the sandbox:

| Mode | Meaning |
|------|---------|
| `read-only` | Agent can read files but not modify them |
| `read-write` | Agent has full access to the directory |

## Process policy

| Setting | Description |
|---------|-------------|
| `run_as_user` | UID the sandbox process runs as |
| `run_as_group` | GID the sandbox process runs as |
| `landlock_compatibility` | Landlock ABI compatibility level for kernel-enforced filesystem restrictions |

## Applying presets

ShoreGuard ships with bundled policy templates for common services (PyPI, npm,
Slack, GitHub, Docker, and more). Click **Apply Preset** in the policy editor
to merge a template into the current policy without overwriting existing rules.

## Revision history

Every policy change creates a new revision. You can view previous versions in
the policy detail view to understand what changed and when.
