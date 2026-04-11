# openshell-cluster Helm chart — **internal test fixture**

> **⚠️ Not a supported install path.** This chart is an internal test
> fixture for exercising ShoreGuard's M12 federation code path in local
> kind clusters and CI. It wraps the `ghcr.io/nvidia/openshell/cluster`
> all-in-one image (k3s-in-container / "Inception") and is **not** how
> you should deploy OpenShell in production.
>
> **Production deployment path:** install NVIDIA's upstream OpenShell
> Helm chart directly into your k8s cluster, then install
> [`charts/shoreguard`](../../../../charts/shoreguard) alongside it,
> and register the gateway endpoints via the ShoreGuard UI or API.
> ShoreGuard does not own, ship, or version the upstream OpenShell chart.
>
> This fixture exists only because:
>
> 1. Without it we cannot exercise `scripts/m12_demo.py` in local/CI
>    runs (the alternative would be a hard dependency on NVIDIA's
>    upstream Helm repo being reachable and version-compatible).
> 2. CI would otherwise only render-test the federation path; the
>    fixture lets a future `helm-e2e` job drive the full A-J walk as a
>    regression gate.
> 3. Contributors working on ShoreGuard's federation code can `helm
>    install` a working gateway in 30 seconds instead of navigating
>    NVIDIA's separate install docs.
>
> All three are ShoreGuard-internal concerns. End users should never
> see this chart.

## What it does

Mirrors the docker-compose `init-openshell` + `init-gateway` pattern
from [`deploy/docker-compose.yml`](../../../../deploy/docker-compose.yml)
in a k8s-native shape:

- Privileged StatefulSet running `ghcr.io/nvidia/openshell/cluster`
  with a PVC on `/var/lib/rancher`, seeded from the image layer via an
  initContainer (see the `seed-rancher` container in
  `templates/statefulset.yaml`).
- Helm post-install Job (`alpine/k8s:1.30.0`) that `kubectl exec`s into
  the cluster pod to generate a CA + server cert + client cert with
  openssl, applies the k3s-internal secrets OpenShell expects
  (`openshell-server-tls`, `openshell-server-client-ca`,
  `openshell-client-tls`, `openshell-ssh-handshake`), and exports the
  client material to an outer-namespace Secret
  `<release>-openshell-cluster-client-tls` so that `scripts/m12_demo.py`
  can read it via `kubectl get secret` and pass it to
  `/api/gateway/register`.
- ClusterIP Service on 30051 and a busybox `nc -zv` `helm test` hook.

## Fixture usage

```bash
# Prereq: a kind / k3d cluster with calico (kindnet doesn't enforce
# NetworkPolicies, which you want for a realistic M12 walk).
kubectl create ns shoreguard
kubectl label ns shoreguard \
  pod-security.kubernetes.io/enforce=privileged --overwrite

helm install cluster-dev ./tests/fixtures/charts/openshell-cluster \
  --namespace shoreguard \
  --set label.env=dev

helm install cluster-staging ./tests/fixtures/charts/openshell-cluster \
  --namespace shoreguard \
  --set label.env=staging

kubectl -n shoreguard wait --for=condition=Complete job \
  -l openshell.io/bootstrap=true --timeout=300s

helm test cluster-dev   -n shoreguard
helm test cluster-staging -n shoreguard
```

Then install `charts/shoreguard` in the same namespace and run
`scripts/m12_demo.py` to drive the full federation demo. See
[`scripts/m12-federation.md`](../../../../scripts/m12-federation.md)
for the end-to-end runbook.

## Values reference

| Key | Default | Purpose |
|---|---|---|
| `image.repository` | `ghcr.io/nvidia/openshell/cluster` | Upstream OpenShell all-in-one image |
| `image.tag` | `0.0.26` | Tracks the project OpenShell pin |
| `label.env` | **required** | Gateway env label — used as `openshell.io/env` pod label and as the `env` label on the registered gateway. `scripts/m12_demo.py` Phase D asserts label filtering with this value. |
| `service.port` | `30051` | k3s NodePort for the inner openshell gRPC service |
| `persistence.size` | `5Gi` | `/var/lib/rancher` PVC size |
| `persistence.storageClassName` | `""` | Leave empty to use the cluster default |
| `containerSecurityContext.privileged` | `true` | Required — k3s will not start without it |
| `bootstrap.enabled` | `true` | Run the post-install mTLS bootstrap Job |
| `bootstrap.image.repository` | `alpine/k8s` | Used to `kubectl exec` into the cluster pod |
| `bootstrap.image.tag` | `1.30.0` | (`bitnami/kubectl` was taken down from docker.io in early 2026) |
| `bootstrap.clientSecretName` | `""` → `<fullname>-client-tls` | Outer Secret holding exported client cert material |
| `bootstrap.k3sReadyTimeoutSeconds` | `300` | Maximum wait for k3s API inside the pod |
| `tests.enabled` | `true` | Enable the `nc -zv` TCP helm-test hook |
| `tests.image.repository` | `busybox` | |

## Known gotchas

- **Pod Security admission.** On clusters that enforce `baseline` or
  `restricted` PSS for user namespaces, the privileged container will
  be rejected. Label the namespace
  `pod-security.kubernetes.io/enforce=privileged` before install.
- **kind default CNI (kindnet)** does not enforce NetworkPolicies —
  the bootstrap Job and ShoreGuard reach the cluster pod
  unconditionally. Install Calico or Cilium to exercise the M12
  NetworkPolicy surface.
- **Helm hook RBAC ordering.** The ServiceAccount/Role/RoleBinding use
  `hook-weight: 0` and the Job uses `hook-weight: 5` so RBAC exists
  before the Job runs. Do not reorder.
- **`seed-rancher` initContainer.** k8s PVC mounts shadow the image's
  baked `/var/lib/rancher` content (unlike Docker named volumes which
  get pre-populated by Docker on first use). Without the init the
  main container crashloops with `cp: cannot create regular file
  /var/lib/rancher/k3s/server/static/charts/: No such file or
  directory`.
- **Inception overhead.** Double kube-proxy iptables traversals add
  10-15% network overhead for in-cluster gRPC. Fine for a test
  fixture, unacceptable for real production scale.
