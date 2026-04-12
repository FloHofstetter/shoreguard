# SBOM / Supply-Chain Viewer

ShoreGuard hosts one CycloneDX Software Bill of Materials per
sandbox and lets operators browse components, licenses, and known
vulnerabilities directly in the UI. Uploads happen from CI —
ShoreGuard does **not** pull SBOMs from gateways, because OpenShell
v0.0.26 has no SBOM RPC and CI knows which build is actually
deploying.

## What it solves

When a denial fires in production, the first question is usually
"what's actually inside the sandbox?". Digging through a container
image to answer that takes minutes you don't have. The SBOM viewer
answers it in one click, and filters vulnerabilities down to the
ones that actually matter for triage.

## Uploading from CI

The expected pattern: generate CycloneDX in your existing scan step
(Trivy, Syft, Grype, etc.), then `POST` the payload to ShoreGuard.

```bash
trivy image --format cyclonedx ghcr.io/acme/agent:$GIT_SHA > sbom.json

curl -X POST \
  -H "Authorization: Bearer $SHOREGUARD_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @sbom.json \
  "$SHOREGUARD_URL/api/gateways/dev/sandboxes/agent-a/sbom"
```

Admin-only. Max payload 10 MiB. Uploads **replace** the prior
snapshot — historical snapshots are deliberately out of scope (if
you need them, archive in object storage).

## Browsing

On the sandbox detail page, open the **SBOM** tab:

- **Components** — paginated table with debounced search and
  severity chips. `CLEAN` filters to components with zero known
  vulnerabilities.
- **Vulnerabilities** — CVE cards sorted highest-severity first,
  with reference links back to the CycloneDX source.
- **Raw** — download the original CycloneDX JSON
  (`application/vnd.cyclonedx+json`).
- **Replace / Delete** — admin-only.

## How vulnerabilities are resolved

ShoreGuard reads vulnerabilities **offline** from the CycloneDX
document's `vulnerabilities` array. There is no online NVD/OSV
lookup. The trade-off: you get whatever your scanner put in the
upload and nothing else; there is no background drift from newly
published CVEs unless CI re-uploads.

This matches the ingestion model — CI decides what's in the sandbox
*and* what's known about it, in one payload.

## Reference

- API: [`/api/gateways/{gw}/sandboxes/{name}/sbom`](../reference/api.md#sbom-m21-v0302)
- Demo: `scripts/m21_demo.py` walks 8 phases against a bundled
  fixture (`scripts/fixtures/sample_cyclonedx.json`).
- Audit events: `sbom.uploaded`, `sbom.deleted`.
