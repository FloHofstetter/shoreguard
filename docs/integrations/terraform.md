# Terraform Provider

The ShoreGuard Terraform provider lets you manage gateways, sandboxes, and
policies as code — giving you reproducible setups, reviewable changes, and a
path to GitOps.

**Provider repository:**
<https://github.com/FloHofstetter/terraform-provider-shoreguard>

## Authentication

Create a service principal with the **admin** or **operator** role:

```bash
shoreguard create-service-principal terraform --role operator
```

The command prints an API key. Store it securely — it cannot be retrieved
again. Pass it to the provider via the `SHOREGUARD_API_KEY` environment
variable or directly in the provider configuration.

## Example

```hcl
terraform {
  required_providers {
    shoreguard = {
      source = "FloHofstetter/shoreguard"
    }
  }
}

provider "shoreguard" {
  endpoint = "http://localhost:8888"
  api_key  = var.shoreguard_api_key
}

resource "shoreguard_gateway" "lab" {
  name     = "lab-gpu-01"
  endpoint = "10.0.1.42:8443"
}

resource "shoreguard_provider" "nvidia" {
  gateway = shoreguard_gateway.lab.name
  name    = "nvidia-a100"
}

resource "shoreguard_sandbox" "dev" {
  gateway  = shoreguard_gateway.lab.name
  name     = "dev-sandbox"
  image    = "nvcr.io/nvidia/pytorch:24.01-py3"
  provider_name = shoreguard_provider.nvidia.name
}

resource "shoreguard_sandbox_policy" "dev_policy" {
  gateway = shoreguard_gateway.lab.name
  sandbox = shoreguard_sandbox.dev.name
  preset  = "pypi"
}
```

## Resources

| Resource | Description |
|----------|-------------|
| `shoreguard_gateway` | Register and manage a gateway |
| `shoreguard_sandbox` | Create and manage a sandbox |
| `shoreguard_provider` | Manage compute providers on a gateway |
| `shoreguard_sandbox_policy` | Apply a network policy to a sandbox |

## Data sources

| Data source | Description |
|-------------|-------------|
| `shoreguard_sandbox` | Look up an existing sandbox |
| `shoreguard_provider` | Look up an existing provider |
| `shoreguard_preset` | Read a single policy preset |
| `shoreguard_presets` | List all available policy presets |
