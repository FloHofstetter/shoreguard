# ShoreGuard Helm chart (M10 MVP)

Minimum-viable Helm chart that gets a single-replica ShoreGuard instance
running on a k8s cluster. Intended as the **M10** deliverable of the
helm/k8s distribution milestone — it exists so interested operators can
try ShoreGuard in their cluster in ~5 minutes.

> **Not for production.** This chart runs SQLite in an `emptyDir` volume,
> has no Ingress by default, no TLS, and no state persistence across pod
> restarts. Production-shaped features (PVC, cert-manager, multi-replica,
> helm test, NetworkPolicy) land in the **M11** chart preset.

## Quickstart (kind)

```bash
kind create cluster --name sg-m10

helm install sg ./charts/shoreguard \
  --set admin.password=changeme

kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=shoreguard --timeout=120s

kubectl port-forward svc/sg-shoreguard 8888:8888 &
curl -sf http://127.0.0.1:8888/healthz     # {"status":"ok"}
curl -sf http://127.0.0.1:8888/version     # {"version":"0.30.0",...}

# Browser: http://127.0.0.1:8888  →  admin@localhost / changeme
```

Cleanup:

```bash
helm uninstall sg
kind delete cluster --name sg-m10
```

## Values

| Key | Default | Notes |
| --- | --- | --- |
| `image.repository` | `ghcr.io/flohofstetter/shoreguard` | GHCR image pushed by the release workflow. |
| `image.tag` | `""` | Empty → `Chart.yaml` `appVersion`. |
| `image.pullPolicy` | `IfNotPresent` | |
| `admin.password` | `""` | **Required.** Chart render fails if empty. |
| `secretKey` | `""` | Empty → 48-char random generated on first install, reused across upgrades via `lookup`. |
| `database.url` | `""` | Empty → SQLite in `emptyDir` (state lost on restart). Set to `postgresql://...` for a real DB. |
| `service.type` | `ClusterIP` | |
| `service.port` | `8888` | |
| `ingress.enabled` | `false` | M10 never renders an Ingress by default. |
| `logLevel` | `info` | One of `critical`, `error`, `warning`, `info`, `debug`, `trace`. |
| `logFormat` | `text` | `text` for humans, `json` for log aggregators. |
| `resources.requests` | `cpu=100m, memory=256Mi` | |
| `resources.limits` | `cpu=1, memory=1Gi` | |
| `podSecurityContext` | `runAsNonRoot=true, runAsUser=1000` | Matches the container image. |
| `containerSecurityContext.readOnlyRootFilesystem` | `true` | All writes go to the `data` and `tmp` `emptyDir`s. |

See [`values.yaml`](./values.yaml) for the full list.

## What happens on install

1. A `Secret` is created with `admin-password` and `secret-key`. The
   secret key is either the value you set, or a 48-char random generated
   on first install (preserved across upgrades).
2. A `ConfigMap` sets `SHOREGUARD_LOG_LEVEL`, `SHOREGUARD_LOG_FORMAT`,
   `SHOREGUARD_HOST`, `SHOREGUARD_PORT`, `SHOREGUARD_RELOAD`. When
   `database.url` is empty, it also sets
   `SHOREGUARD_ALLOW_UNSAFE_CONFIG=true` because ShoreGuard refuses to
   start with SQLite in a prod-like deploy by default
   (`shoreguard/settings.py:enforce_production_safety`).
3. A `Deployment` with `replicas: 1` and `strategy: Recreate` runs the
   image as the non-root `shoreguard` user (uid 1000) with a read-only
   root filesystem and drops all capabilities.
4. A `Service` of type `ClusterIP` exposes port 8888 as `http`.
5. A `ServiceAccount` is created with `automountServiceAccountToken: false`.

Probes:

* `startupProbe` → `/healthz`, up to 60 s before the liveness gate kicks in.
* `livenessProbe` → `/healthz`, every 10 s.
* `readinessProbe` → `/readyz`, every 5 s, with a 30 s error budget.

## Provisioning an LLM provider

The Anthropic (or other LLM) API key is **not** a chart value. ShoreGuard
stores provider credentials in its database and exposes them via the
Settings UI at `/settings` or the API endpoint `POST /api/gateways/{gw}/providers`.
After `helm install`, log in and add the provider there.

## Known limitations (M10)

| Limitation | Planned for |
| --- | --- |
| SQLite state lost on pod restart | M11 (PVC preset) |
| No TLS / Ingress by default | M11 (cert-manager preset) |
| Single replica only | M11 (multi-replica + hard-fail secret key check) |
| No `helm test` hook | M11 |
| No federation (multi-gateway inside cluster) | M12 |

## CI

`helm lint charts/shoreguard` runs on every push/PR via the `helm-lint`
job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
