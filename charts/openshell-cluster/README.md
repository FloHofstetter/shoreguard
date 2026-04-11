# openshell-cluster Helm chart

Sibling chart to [`charts/shoreguard`](../shoreguard) that deploys an
OpenShell cluster (the `k3s-in-container` image
`ghcr.io/nvidia/openshell/cluster`) as a k8s StatefulSet, generates mTLS
material via a post-install bootstrap Job, and exports a client cert so
that a Helm-deployed ShoreGuard can register it as a federated gateway.

Introduced in **M12** — the k8s analog of the M8 host-process federation
demo. See [`../../scripts/m12-federation.md`](../../scripts/m12-federation.md)
for the end-to-end runbook.

## Scope

- Runs a single `privileged` StatefulSet pod with `/var/lib/rancher` on a PVC.
- Post-install Job exec's into the pod to generate CA + server cert + client
  cert and to create k3s-internal Secrets (`openshell-server-tls`,
  `openshell-server-client-ca`, `openshell-client-tls`,
  `openshell-ssh-handshake`) — mirrors [`deploy/docker-compose.yml`](../../deploy/docker-compose.yml)
  `init-openshell`.
- Exports the client mTLS material to an outer k8s Secret
  (`<release>-openshell-cluster-client-tls`) so that `scripts/m12_demo.py`
  can read it via `kubectl get secret` and pass it to
  `/api/gateway/register`.
- Not production-hardened; this chart exists to prove federation in k8s.
  Cross-namespace federation, HA for the cluster pod, and cert rotation
  are explicitly out of scope.

## Quickstart (kind)

```bash
# A ShoreGuard release must already exist in the same namespace.
kubectl create ns shoreguard

helm install cluster-dev ./charts/openshell-cluster \
  --namespace shoreguard \
  --set label.env=dev

helm install cluster-staging ./charts/openshell-cluster \
  --namespace shoreguard \
  --set label.env=staging

# The bootstrap Job runs as a Helm post-install hook. Watch it:
kubectl -n shoreguard get jobs -l openshell.io/bootstrap=true -w

# TCP smoke:
helm test cluster-dev   -n shoreguard
helm test cluster-staging -n shoreguard
```

Then install `charts/shoreguard` in the same namespace and run
`scripts/m12_demo.py` to drive the federation demo.

## Values reference

| Key | Default | Purpose |
|---|---|---|
| `image.repository` | `ghcr.io/nvidia/openshell/cluster` | Upstream OpenShell cluster image |
| `image.tag` | `0.0.26` | Tracks the project OpenShell pin |
| `label.env` | **required** | Gateway env label — used as `openshell.io/env` pod label AND as the `env` label on the registered gateway. Federation label-filter assertions depend on it. |
| `service.port` | `30051` | k3s gRPC port |
| `persistence.size` | `5Gi` | `/var/lib/rancher` PVC size |
| `persistence.storageClassName` | `""` | Leave empty to use the cluster default |
| `containerSecurityContext.privileged` | `true` | Required — k3s will not start without it |
| `bootstrap.enabled` | `true` | Run the post-install mTLS bootstrap Job |
| `bootstrap.image.repository` | `bitnami/kubectl` | Used to `kubectl exec` into the cluster pod |
| `bootstrap.image.tag` | `1.30` | |
| `bootstrap.clientSecretName` | `""` → `<fullname>-client-tls` | Outer Secret holding exported client cert material |
| `bootstrap.k3sReadyTimeoutSeconds` | `300` | Maximum wait for k3s API inside the pod |
| `tests.enabled` | `true` | Enable the TCP helm-test hook |
| `tests.image.repository` | `busybox` | |

## What happens on install

1. `StatefulSet` with one privileged pod starts; `/var/lib/rancher` mounts a PVC.
2. Helm post-install hook `ServiceAccount` + `Role` + `RoleBinding` are
   created (hook-weight 0) with `pods/exec` verb + `secrets` create/update.
3. The bootstrap `Job` (hook-weight 5) runs:
   - `kubectl wait --for=condition=Ready pod/<fullname>-0`
   - Polls `kubectl exec <fullname>-0 -- kubectl get nodes` until k3s is up
     (`bootstrap.k3sReadyTimeoutSeconds` budget, 3s intervals)
   - Waits for the `openshell` namespace inside k3s
   - `openssl` generates CA + server + client certs in `/certs` inside the pod
     (idempotent: skips if files already exist)
   - Creates / updates k3s-internal Secrets via `kubectl apply --dry-run=client`
   - Exports base64-encoded `ca.crt` / `client.crt` / `client.key` as the
     outer Secret `<release>-openshell-cluster-client-tls`
4. `helm test` launches a busybox pod that runs `nc -zv <svc> 30051`.

## Consuming the client Secret (scripts/m12_demo.py)

```bash
NAMESPACE=shoreguard
RELEASE=cluster-dev
CA=$(kubectl -n "$NAMESPACE" get secret "${RELEASE}-openshell-cluster-client-tls" \
       -o jsonpath='{.data.ca\.crt}')
# → already base64-encoded, pass straight into the register payload.
```

## Known gotchas

- **Pod Security admission.** On clusters that enforce `baseline` or
  `restricted` PSS for user namespaces, the privileged container will be
  rejected. Label the namespace with
  `pod-security.kubernetes.io/enforce=privileged` before install.
- **kind default CNI (kindnet)** does not enforce NetworkPolicies — the
  bootstrap Job and ShoreGuard reach the cluster pod unconditionally.
  Install Calico or Cilium if you want to validate network isolation.
- **Helm hook RBAC ordering.** The ServiceAccount/Role/RoleBinding use
  `hook-weight: 0` and the Job uses `hook-weight: 5` so RBAC exists
  before the Job runs. Do not reorder.
- **Image pull.** `ghcr.io/nvidia/openshell/cluster:0.0.26` is public;
  anonymous pulls work. If it is ever gated behind auth, set
  `image.pullSecrets`.
