# Production Kubernetes Deployment

This runbook walks through deploying ShoreGuard alongside NVIDIA OpenShell
gateways on a production Kubernetes cluster. The two are installed as
**separate Helm releases** — ShoreGuard does not bundle or manage NVIDIA's
chart.

What this guide covers:

- Installing NVIDIA's upstream OpenShell Helm chart (pointer to their docs)
- Installing `charts/shoreguard` with the production preset
- Registering gateways with mTLS
- Verifying the deployment end-to-end

What this guide does **not** cover: umbrella charts, GitOps manifests,
Terraform modules, or operator-based deployments.

---

## Prerequisites

| Requirement | Minimum | Notes |
|---|---|---|
| Kubernetes | >= 1.25 | Tested against kind v1.32 |
| Helm | >= 3.12 | `helm version` |
| kubectl | matching cluster version | |
| CNI | calico or cilium | Must enforce NetworkPolicy — kindnet and basic flannel do **not** |
| cert-manager | >= 1.14 | With a `ClusterIssuer` named `letsencrypt-prod` |
| Ingress controller | ingress-nginx | The production preset sets `ingress.className: nginx` |
| StorageClass | any RWO-capable | Cluster default works; override via `persistence.storageClassName` |

!!! note "Pod Security Standards"
    ShoreGuard runs under the `restricted` PSS profile (non-root, read-only
    root filesystem, all capabilities dropped). NVIDIA's OpenShell chart
    may require a `privileged` namespace — check their docs and label the
    namespace accordingly:
    `kubectl label ns openshell pod-security.kubernetes.io/enforce=privileged`

---

## Step 1 — Install NVIDIA OpenShell

Install NVIDIA's upstream OpenShell Helm chart into a dedicated namespace.
ShoreGuard tracks the latest upstream stable tag and is currently tested
against **OpenShell v0.0.32** — pin to that version unless you have
verified interop with a newer release. The protobuf wire surface has not
changed since `v0.0.30`, so any gateway `≥ v0.0.30` is acceptable.

!!! tip "Where to find the chart"
    Refer to [NVIDIA's OpenShell documentation](https://github.com/NVIDIA/openshell)
    for installation instructions. ShoreGuard does not ship, vendor, or
    manage NVIDIA's chart — install it exactly as NVIDIA documents.

```bash
kubectl create ns openshell
kubectl label ns openshell pod-security.kubernetes.io/enforce=privileged

# Install NVIDIA's chart per their docs, e.g.:
# helm install gw-prod nvidia/openshell --namespace openshell \
#   --set <nvidia-specific-values>
```

After installation, verify the gateway pod is running and the mTLS Secrets
have been created:

```bash
kubectl -n openshell get pods
kubectl -n openshell get secrets | grep tls
```

Note the Secret name(s) containing the client TLS material — you will need
them in Step 4.

---

## Step 2 — Create the ShoreGuard namespace and secrets

```bash
kubectl create ns shoreguard
```

Create a Secret with the admin bootstrap password and session HMAC key:

```bash
kubectl -n shoreguard create secret generic sg-secrets \
  --from-literal=admin-password="$(openssl rand -hex 16)" \
  --from-literal=secret-key="$(openssl rand -hex 32)"
```

!!! tip "Secret management"
    For production, consider External Secrets Operator, Sealed Secrets, or
    HashiCorp Vault instead of plain `kubectl create secret`. The chart
    accepts any Secret that contains the keys `admin-password` and
    `secret-key`.

Save the admin password — you will need it to log in:

```bash
kubectl -n shoreguard get secret sg-secrets \
  -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

---

## Step 3 — Install ShoreGuard

Use `values.production.yaml` as the base and override the values specific
to your environment:

```bash
helm install sg ./charts/shoreguard \
  --namespace shoreguard \
  -f charts/shoreguard/values.production.yaml \
  --set existingSecret=sg-secrets \
  --set ingress.host=shoreguard.example.com \
  --set 'ingress.tls[0].hosts[0]=shoreguard.example.com' \
  --set 'ingress.tls[0].secretName=shoreguard-tls' \
  --set networkPolicy.egress.inClusterGateways.enabled=true \
  --set 'networkPolicy.egress.inClusterGateways.namespaceSelector.matchLabels.kubernetes\.io/metadata\.name=openshell'
```

Replace `shoreguard.example.com` with your actual domain. The production
preset enables:

- **Persistence** — 2 Gi PVC at `/home/shoreguard/.config/shoreguard`
- **Ingress** — nginx class with cert-manager TLS and force-SSL-redirect
- **NetworkPolicy** — DNS + LLM providers (443/tcp non-RFC1918) egress
- **JSON logging** — structured output for log aggregation
- **Forwarded header trust** — `forwardedAllowIps: "*"` so the app sees
  the real client IP through the Ingress controller

The `inClusterGateways` egress rule opens TCP 30051 to pods in the
`openshell` namespace, allowing ShoreGuard to reach the gateways through
the NetworkPolicy.

!!! note "Optional: podSelector for gateway egress"
    If you want to restrict egress to specific gateway pods (not all pods
    in the namespace), add a `podSelector`:

    ```bash
    --set 'networkPolicy.egress.inClusterGateways.podSelector.matchLabels.app\.kubernetes\.io/name=openshell'
    ```

    Check `kubectl -n openshell get pods --show-labels` for the correct
    label key/value from NVIDIA's chart.

Wait for the rollout and run the built-in helm test:

```bash
kubectl -n shoreguard wait --for=condition=ready pod \
  -l app.kubernetes.io/name=shoreguard --timeout=120s

helm test sg --namespace shoreguard
```

### Optional overrides

| Override | When to use |
|---|---|
| `--set database.url=postgresql://...` | External Postgres (required for multi-replica) |
| `--set replicaCount=2` | Horizontal scaling — needs `database.url` + `existingSecret` or explicit `secretKey` |
| `--set podDisruptionBudget.enabled=true` | Enable PDB when running multiple replicas |
| `--set persistence.storageClassName=...` | Non-default StorageClass |
| `--set networkPolicy.egress.postgres.enabled=true` | In-cluster Postgres behind NetworkPolicy |

!!! note "Multi-replica footguns"
    The chart **refuses to render** if `replicaCount > 1` without
    `database.url` (SQLite + RWO PVC deadlock) or without a stable
    `secretKey` / `existingSecret` (session HMAC mismatch across replicas).

---

## Step 4 — Register gateways

Once ShoreGuard is running, register each OpenShell gateway with its mTLS
credentials. You can do this via the **ShoreGuard UI** or via `curl`.

### Extract mTLS material

Find the client TLS Secret that NVIDIA's chart created:

```bash
kubectl -n openshell get secrets | grep client-tls
```

!!! note "Secret name"
    The exact Secret name depends on NVIDIA's chart version and your Helm
    release name. Look for a Secret containing `ca.crt`, `tls.crt`, and
    `tls.key` (or similar keys). Adjust the `jsonpath` expressions below
    to match.

Extract and base64-encode the certificates:

```bash
export GW_RELEASE=gw-prod   # your NVIDIA Helm release name
export GW_NS=openshell

CA_CERT=$(kubectl -n "$GW_NS" get secret "${GW_RELEASE}-client-tls" \
  -o jsonpath='{.data.ca\.crt}')
CLIENT_CERT=$(kubectl -n "$GW_NS" get secret "${GW_RELEASE}-client-tls" \
  -o jsonpath='{.data.tls\.crt}')
CLIENT_KEY=$(kubectl -n "$GW_NS" get secret "${GW_RELEASE}-client-tls" \
  -o jsonpath='{.data.tls\.key}')
```

### Register via curl

```bash
# Determine the ShoreGuard URL
SG_URL="https://shoreguard.example.com"
# Or via port-forward:
# kubectl -n shoreguard port-forward svc/sg-shoreguard 8888:8888 &
# SG_URL="http://127.0.0.1:8888"

# Retrieve admin password
ADMIN_PW=$(kubectl -n shoreguard get secret sg-secrets \
  -o jsonpath='{.data.admin-password}' | base64 -d)

# Login
curl -c cookies.txt -X POST "${SG_URL}/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@localhost\",\"password\":\"${ADMIN_PW}\"}"

# Register gateway
curl -b cookies.txt -X POST "${SG_URL}/api/gateway/register" \
  -H 'Content-Type: application/json' \
  -d "{
    \"name\": \"${GW_RELEASE}\",
    \"endpoint\": \"${GW_RELEASE}-openshell.${GW_NS}.svc.cluster.local:30051\",
    \"scheme\": \"https\",
    \"auth_mode\": \"mtls\",
    \"labels\": {\"env\": \"production\"},
    \"ca_cert\": \"${CA_CERT}\",
    \"client_cert\": \"${CLIENT_CERT}\",
    \"client_key\": \"${CLIENT_KEY}\"
  }"
```

!!! tip "Registering via the UI"
    Navigate to **Gateways** in the ShoreGuard UI, click **Register
    Gateway**, and paste the endpoint, certificates, and labels into the
    form. The UI accepts the same fields as the API.

Repeat for each gateway, adjusting the release name, endpoint, and labels.

---

## Step 5 — Verify

Run through this checklist after registration:

- [ ] **Helm test passes:**
      `helm test sg --namespace shoreguard`

- [ ] **Gateway connected:**
      ```bash
      curl -b cookies.txt "${SG_URL}/api/gateway/list"
      ```
      Each gateway should show `"status": "connected"`.

- [ ] **Label filter works:**
      ```bash
      curl -b cookies.txt "${SG_URL}/api/gateway/list?label=env:production"
      ```

- [ ] **UI login works** at `https://shoreguard.example.com` with
      `admin@localhost` and the password from Step 2.

- [ ] **TLS certificate issued:**
      ```bash
      kubectl -n shoreguard get certificate
      ```
      The certificate should show `Ready=True`.

- [ ] **Persistence survives restart:**
      ```bash
      kubectl -n shoreguard rollout restart deployment/sg-shoreguard
      kubectl -n shoreguard wait --for=condition=ready pod \
        -l app.kubernetes.io/name=shoreguard --timeout=120s
      curl -b cookies.txt "${SG_URL}/api/gateway/list"
      ```
      Gateways and settings should still be present.

---

## Day-2 operations

### Scaling to multiple replicas

1. Set up an external PostgreSQL database.
2. Upgrade with `--set database.url=postgresql://... --set replicaCount=2 --set podDisruptionBudget.enabled=true`.
3. The chart validates that `secretKey` or `existingSecret` is set when
   `replicaCount > 1`.

### Secret rotation

Updating `sg-secrets` does **not** trigger a pod restart. After rotating
keys:

```bash
kubectl -n shoreguard rollout restart deployment/sg-shoreguard
```

### Further reading

- [Rollback Runbook](../admin/rollback.md)
- [Troubleshooting](../admin/troubleshooting.md)
- [Prometheus Metrics](../integrations/prometheus.md)
- [Configuration Reference](../reference/configuration.md)
- [Chart README](https://github.com/FloHofstetter/shoreguard/tree/main/charts/shoreguard)
