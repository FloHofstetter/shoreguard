# ShoreGuard Helm chart

Helm chart for deploying ShoreGuard on Kubernetes. Ships two usage modes:

* **Default (`values.yaml`)** — single-replica, SQLite-in-`emptyDir`, no
  Ingress. Good for a 5-minute kind demo.
* **Production (`values.production.yaml`)** — PVC-backed data path,
  nginx-ingress + cert-manager, NetworkPolicy, `helm test` hook,
  forwarded-proto trust enabled.

The chart has been exercised against kind v1.32 with calico (for
NetworkPolicy enforcement), cert-manager v1.16, and ingress-nginx.

## Quickstart (kind, default values)

```bash
kind create cluster --name sg-demo

helm install sg ./charts/shoreguard \
  --set admin.password=changeme

kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=shoreguard --timeout=120s

kubectl port-forward svc/sg-shoreguard 8888:8888 &
curl -sf http://127.0.0.1:8888/healthz     # {"status":"ok"}
curl -sf http://127.0.0.1:8888/version

helm test sg                                # runs the test-connection pod
```

Browser: http://127.0.0.1:8888 → `admin@localhost` / `changeme`.

Cleanup:

```bash
helm uninstall sg
kind delete cluster --name sg-demo
```

## Production quickstart

Requires cert-manager + an Ingress controller already installed in the
cluster.

```bash
# 1. BYO Secret — ShoreGuard will NOT manage it for you
kubectl create secret generic sg-secrets \
  --from-literal=admin-password=$(openssl rand -hex 16) \
  --from-literal=secret-key=$(openssl rand -hex 32)

# 2. Install with the production preset
helm install sg ./charts/shoreguard \
  -f charts/shoreguard/values.production.yaml \
  --set ingress.host=shoreguard.example.com \
  --set existingSecret=sg-secrets

helm test sg

# 3. Verify state survives a rollout
kubectl rollout restart deployment/sg-shoreguard
kubectl rollout status deployment/sg-shoreguard --timeout=120s
curl -sk https://shoreguard.example.com/api/gateway/list
```

The preset enables a PVC for `/home/shoreguard/.config/shoreguard`, a
NetworkPolicy that allows traffic from the `ingress-nginx` namespace
and 443/tcp egress to non-RFC1918 destinations (for LLM provider
APIs), and structured JSON logging.

## Values

### Core

| Key | Default | Notes |
| --- | --- | --- |
| `replicaCount` | `1` | Bumping this requires `secretKey`/`existingSecret` and `database.url` set to an external Postgres. |
| `image.repository` | `ghcr.io/flohofstetter/shoreguard` | GHCR image pushed by the release workflow. |
| `image.tag` | `""` | Empty → `Chart.yaml` `appVersion`. |
| `image.pullPolicy` | `IfNotPresent` | |
| `admin.password` | `""` | **Required** unless `existingSecret` is set. |
| `secretKey` | `""` | Empty → 48-char random generated on first install, reused across upgrades via `lookup`. Ignored when `existingSecret` is set. |
| `existingSecret` | `""` | Bring-your-own Secret name (must contain `admin-password` + `secret-key` keys). Mutually exclusive with `admin.password` and `secretKey`. |
| `database.url` | `""` | Empty → SQLite (use `persistence.enabled=true` for durability). Set to `postgresql://...` for Postgres. |
| `forwardedAllowIps` | `"127.0.0.1"` | Uvicorn `forwarded_allow_ips`. Set to `"*"` when serving behind a k8s Ingress — the default ignores headers from non-loopback proxies. |

### Networking

| Key | Default | Notes |
| --- | --- | --- |
| `service.type` | `ClusterIP` | |
| `service.port` | `8888` | |
| `ingress.enabled` | `false` | Off by default. The production preset enables it with cert-manager. |
| `ingress.className` | `""` | Set to `nginx` for ingress-nginx. |
| `ingress.annotations` | `{}` | Cert-manager / proxy annotations live here. |
| `ingress.host` | `shoreguard.local` | |
| `ingress.tls` | `[]` | Standard Ingress TLS block. |
| `networkPolicy.enabled` | `false` | Off by default. Requires a CNI that enforces NetworkPolicies — kindnet does NOT. |
| `networkPolicy.ingressNamespaceSelector` | `ingress-nginx` | Matches the namespace label of the Ingress controller pod. |
| `networkPolicy.egress.dns.enabled` | `true` | Allows 53/udp+tcp to `kube-system`. |
| `networkPolicy.egress.llmProviders.enabled` | `true` | Allows 443/tcp to non-RFC1918 CIDRs. Required for Anthropic/OpenAI/etc. |
| `networkPolicy.egress.postgres.enabled` | `false` | Enable + configure `podSelector`/`namespaceSelector` for an in-cluster Postgres. |
| `networkPolicy.egress.extra` | `[]` | Escape hatch for custom egress rules. |

### Persistence and disruption

| Key | Default | Notes |
| --- | --- | --- |
| `persistence.enabled` | `false` | `false` → `emptyDir` (state lost on restart). |
| `persistence.storageClassName` | `""` | Empty → cluster default. |
| `persistence.size` | `1Gi` | |
| `persistence.accessMode` | `ReadWriteOnce` | Only RWO is supported; see footgun note below. |
| `persistence.existingClaim` | `""` | Bring-your-own PVC. |
| `podDisruptionBudget.enabled` | `false` | Only renders when `replicaCount > 1`. |
| `podDisruptionBudget.minAvailable` | `1` | Must be less than `replicaCount` — equal or greater deadlocks drains, the chart fails rendering. |

### Tests

| Key | Default | Notes |
| --- | --- | --- |
| `tests.enabled` | `true` | Renders a `helm test` hook pod that curls `/healthz` and `/version` against the Service. |
| `tests.image.repository` | `curlimages/curl` | |
| `tests.image.tag` | `8.10.1` | |

See [`values.yaml`](./values.yaml) for the full list.

## Multi-replica footgun (important)

`replicaCount > 1` is a coordinated change. Two things MUST be true:

1. `database.url` is set to an **external** Postgres. SQLite in a
   `ReadWriteOnce` PVC can only attach to one pod — the second
   replica deadlocks trying to mount the same volume.
2. A **stable** `secretKey` (or an `existingSecret` that provides
   `secret-key`) is set. Otherwise each replica derives its own
   on-disk session HMAC and every load-balancer decision breaks the
   user's session.

The chart catches both footguns at `helm template` time via
`fail` calls in `templates/_helpers.tpl:shoreguard.validate`, and
the backend's `check_production_readiness()` raises `RuntimeError`
at pod startup if `SHOREGUARD_REPLICAS > 1` and
`SHOREGUARD_SECRET_KEY` is unset.

## BYO Secret (`existingSecret`)

Useful when an external system (Vault, External Secrets Operator,
sealed-secrets, GitOps) manages the Secret:

```bash
kubectl create secret generic sg-secrets \
  --from-literal=admin-password=<password> \
  --from-literal=secret-key=<32+ char random>

helm install sg ./charts/shoreguard \
  -f charts/shoreguard/values.production.yaml \
  --set existingSecret=sg-secrets \
  --set ingress.host=shoreguard.example.com
```

**Caveat:** rotating keys inside an externally-managed Secret does
NOT trigger a pod restart. The Deployment's `checksum/secret`
annotation hashes the chart-rendered Secret, which is empty when
`existingSecret` is set. Run
`kubectl rollout restart deployment/<name>` manually after updating
keys.

## What happens on install

1. If `existingSecret` is not set, the chart creates a `Secret` with
   `admin-password` and `secret-key`. The secret key is either the
   value you set, or a 48-char random generated on first install
   (preserved across upgrades via `lookup`). With `existingSecret`,
   no Secret is created.
2. A `ConfigMap` sets the standard `SHOREGUARD_*` env vars. When
   `database.url` is empty, it also sets
   `SHOREGUARD_ALLOW_UNSAFE_CONFIG=true` because ShoreGuard refuses
   to start with SQLite in a prod-like deploy by default
   (`shoreguard/settings.py:enforce_production_safety`).
3. A `Deployment` runs the image as the non-root `shoreguard` user
   (uid 1000) with `readOnlyRootFilesystem: true`, drops all
   capabilities, and uses `strategy: Recreate` for single-replica
   or `RollingUpdate` (maxSurge=1, maxUnavailable=0) for
   multi-replica. Env includes `SHOREGUARD_REPLICAS` (used by the
   prod-readiness checks) and `SHOREGUARD_FORWARDED_ALLOW_IPS`
   (used by uvicorn's proxy_headers trust list).
4. A `Service` of type `ClusterIP` exposes port 8888 as `http`.
5. When `persistence.enabled=true`, a PVC is created (or the
   existing one from `persistence.existingClaim` is used) and
   mounted at `/home/shoreguard/.config/shoreguard`.
6. When `ingress.enabled=true`, an `Ingress` is rendered with the
   configured className, annotations, host and TLS block.
7. When `networkPolicy.enabled=true`, a `NetworkPolicy` restricts
   ingress to the configured ingress-namespace + helm-test pods,
   and egress to DNS + LLM providers + optional Postgres + extras.
8. When `podDisruptionBudget.enabled=true` and `replicaCount > 1`,
   a `PodDisruptionBudget` is created.
9. When `tests.enabled=true`, a `helm test` hook pod is rendered.
10. A `ServiceAccount` is created with
    `automountServiceAccountToken: false`.

Probes:

* `startupProbe` → `/healthz`, up to 60 s before the liveness gate kicks in.
* `livenessProbe` → `/healthz`, every 10 s.
* `readinessProbe` → `/readyz`, every 5 s, with a 30 s error budget.

## Provisioning an LLM provider

The Anthropic (or other LLM) API key is **not** a chart value.
ShoreGuard stores provider credentials in its database and exposes
them via the Settings UI at `/settings` or the API endpoint
`POST /api/gateways/{gw}/providers`. After `helm install`, log in
and add the provider there.

## CI

`helm lint charts/shoreguard` + three `helm template` renders (default
values, production preset with `existingSecret`, and a negative test
asserting the multi-replica footgun guard fires) run on every push/PR
via the `helm-lint` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
