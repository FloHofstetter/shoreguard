# Changelog

All notable changes to Shoreguard are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.30.2] — unreleased

### Fixed

- **Approve → reload race.** The `POST /approve` and `POST /approve-all`
  endpoints now accept a `?wait_loaded=true` query parameter. When set,
  the server polls the gateway's policy status internally (up to 30 s)
  and only returns once the new policy version is reported as `loaded` —
  or 504 on timeout. This eliminates the client-side polling loop that
  was previously required to avoid spurious 403s from the proxy still
  running the old policy. All three demo scripts
  (`m7_demo.py`, `m8_demo.py`, `m12_demo.py`) have been updated to use
  the server-side wait.
- **Local-mode plaintext gateway auto-register.** When
  `SHOREGUARD_LOCAL_MODE=true`, the filesystem gateway importer now skips
  mTLS certificate material for `http://` (plaintext) endpoints. Previously,
  if the OpenShell data directory contained cert files alongside a plaintext
  gateway, they were imported and the connection attempt used TLS against
  a plaintext endpoint, resulting in a permanent `unreachable` status.

## [0.30.1] — 2026-04-12

### Changed

- **Moved `charts/openshell-cluster` → `tests/fixtures/charts/openshell-cluster`.**
  The chart was never a supported production install path — it wraps
  NVIDIA's `ghcr.io/nvidia/openshell/cluster` all-in-one image
  (privileged k3s-in-container, ~10-15% network overhead from double
  iptables NAT) so that `scripts/m12_demo.py` can exercise the M12
  federation code path in local/kind/CI without requiring NVIDIA's
  upstream OpenShell Helm chart at test time. Keeping it under
  `charts/` misled readers into thinking it was a production option.
  The fixture is now clearly scoped as internal test infrastructure:
  its README leads with a "not a supported install path" banner and
  points at the real production pattern (install NVIDIA's upstream
  OpenShell chart separately, then `charts/shoreguard` alongside it).
  CI renames the lint/render block into a dedicated `m12-fixture-lint`
  job so fixture status is tracked distinctly from the supported
  `helm-lint` job. `scripts/m12_demo.py` and `scripts/m12-federation.md`
  reference the new path and carry the same positioning notice.

### Added (M12)

- **Internal M12 federation test fixture at
  `tests/fixtures/charts/openshell-cluster/`.** Runs the upstream
  `ghcr.io/nvidia/openshell/cluster:0.0.26` k3s-in-container image as a
  privileged StatefulSet so the helm-deployed ShoreGuard can federate
  multiple gateways entirely in k8s. A post-install bootstrap Job
  (weight 5, `bitnami/kubectl`) `kubectl exec`'s into the cluster pod,
  generates a CA + server + client mTLS set inside `/certs`, creates
  the k3s-internal secrets `openshell-server-tls`,
  `openshell-server-client-ca`, `openshell-client-tls`, and
  `openshell-ssh-handshake` (idempotent via `kubectl apply
  --dry-run=client`), then exports the client material as an outer-ns
  Secret `<release>-openshell-cluster-client-tls`. `helm test` ships a
  busybox `nc -zv` TCP probe. Chart-time validation fails rendering
  when `label.env` is empty.
- **`scripts/m12_demo.py` — in-k8s federation end-to-end demo.** k8s
  analog of `scripts/m8_demo.py`: reads each gateway's client mTLS
  Secret via `kubectl`, registers both clusters via
  `POST /api/gateway/register` with `auth_mode=mtls` /
  `scheme=https`, then drives the same Phase A–J federation assertions
  (label filter, per-gateway audit attribution, unfiltered audit
  coalescence, `/api/gateway/list` with labels + `status=connected`).
  Sandbox exec steps (Phases F + G) route through ShoreGuard's
  `/api/gateways/{gw}/sandboxes/{sb}/exec` LRO instead of shelling to
  `openshell` CLI, so the host running the demo only needs `kubectl`,
  `helm`, and `uv`.
- **`scripts/m12-federation.md`** — runbook for the M12 demo: kind
  cluster, privileged namespace, two `helm install
  cluster-{dev,staging}`, one `helm install sg`, `kubectl
  port-forward`, the Phase-A-J walk-through, and Phase K (`kubectl
  rollout restart statefulset/cluster-dev-openshell-cluster` while
  driving `cluster-staging` traffic, proving gateway-independence of
  the control plane).
- **CI `m12-fixture-lint` job.** `.github/workflows/ci.yml` now runs
  `helm lint tests/fixtures/charts/openshell-cluster` plus a positive
  render matrix (`label.env=dev` + `label.env=staging`) and a negative
  test asserting empty `label.env` must fail rendering. Job is named
  and scoped separately from the supported `helm-lint` job so fixture
  status never gets mistaken for production chart status.

### Added (docs)

- **Production Kubernetes deployment runbook at
  `docs/deploy/production-k8s.md`.** End-to-end walkable guide for ops
  teams deploying ShoreGuard alongside NVIDIA's upstream OpenShell Helm
  chart on a real k8s cluster. Covers prerequisites (CNI with
  NetworkPolicy enforcement, cert-manager, ingress-nginx), BYO Secret
  pattern, `helm install` with the production preset and all required
  overrides, gateway registration with mTLS material extracted from
  NVIDIA's chart-created Secrets, a post-deploy verification checklist,
  and day-2 operations (multi-replica scaling, secret rotation). Cross-linked
  from `docs/admin/deployment.md`, `charts/shoreguard/README.md`, and
  the MkDocs nav.

### Added (chart)

- **`networkPolicy.egress.inClusterGateways` chart value.** First-class
  egress rule for in-cluster OpenShell gateways (TCP 30051 to private Pod
  IPs). The existing LLM-providers block only allows 443/tcp to
  non-RFC1918 CIDRs, so federated gateways running inside the cluster
  were unreachable unless patched via the `egress.extra` escape hatch.
  New value: `enabled: false` (default off), `port: 30051`,
  `podSelector: {}`, `namespaceSelector: {}`. Point the selectors at
  NVIDIA's upstream OpenShell Helm chart pod labels and flip `enabled:
  true` for in-k8s federation deploys. CI render test added.

### Added (M10 + M11)

- **Helm chart MVP at `charts/shoreguard/`** (M10). Single-replica,
  SQLite-in-emptyDir, no Ingress by default — gets ShoreGuard running on
  a fresh `kind`/`k3d` cluster with `helm install sg ./charts/shoreguard
  --set admin.password=...`. Secret key is generated once per release
  and preserved across upgrades via a `lookup`. `SHOREGUARD_ALLOW_UNSAFE_CONFIG`
  is injected automatically when `database.url` is empty so the pod boots
  past the prod-readiness gate. New `helm-lint` CI job covers `helm lint`
  plus a `helm template` render smoke check.
- **`charts/shoreguard` — M11 production hardening.** Turns the M10 MVP
  chart into something an ops team would actually roll. New values:
  `replicaCount`, `persistence.{enabled,storageClassName,size,accessMode,existingClaim}`,
  `existingSecret` (BYO Secret path), `networkPolicy.*` (ingress-namespace
  selector, DNS/LLM-provider/Postgres/extra egress blocks),
  `podDisruptionBudget.{enabled,minAvailable}`, `tests.{enabled,image}`,
  `forwardedAllowIps`. New templates: `pvc.yaml`, `networkpolicy.yaml`,
  `pdb.yaml`, `tests/test-connection.yaml`. The Deployment switches
  strategy between `Recreate` (single-replica) and `RollingUpdate`
  (`maxSurge=1, maxUnavailable=0` for multi-replica), passes
  `SHOREGUARD_REPLICAS` and `SHOREGUARD_FORWARDED_ALLOW_IPS` to the pod,
  and swaps the `data` volume between `emptyDir` and a PVC based on
  `persistence.enabled`. Chart version bumped `0.1.0 → 0.2.0`,
  `appVersion → 0.30.1`.
- **`charts/shoreguard/values.production.yaml`** — opinionated preset
  that enables PVC + cert-manager + nginx-ingress + NetworkPolicy +
  structured JSON logs + forwarded-headers trust. Single-replica by
  default (the preset is RWO-PVC-shaped); scale out only after setting
  `database.url` to an external Postgres.
- **Chart-time footgun guards (`templates/_helpers.tpl:shoreguard.validate`).**
  `helm template` now fails with a clear message when
  `existingSecret` collides with `admin.password`/`secretKey`, when
  `replicaCount > 1` is combined with `persistence.enabled=true` and no
  `database.url` (RWO-PVC deadlock), or when `replicaCount > 1` is
  combined with no `secretKey`/`existingSecret` (session HMAC drift).
- **`helm test` hook.** `helm test <release>` now runs a tiny
  `curlimages/curl` pod that `curl`s `/healthz` and `/version` against
  the in-cluster Service (not the Ingress — keeps the test independent
  of cluster DNS and TLS trust). Gated on `tests.enabled`.
- **`shoreguard.server.forwarded_allow_ips`** setting (env
  `SHOREGUARD_FORWARDED_ALLOW_IPS`, default `"127.0.0.1"`). Passed to
  uvicorn as `forwarded_allow_ips` together with `proxy_headers=True`,
  so X-Forwarded-Proto/Host from a trusted TLS-terminating proxy is
  honored. Without this, sessions behind nginx-ingress would see
  `http://` internally and issue non-Secure cookies. The production
  chart preset sets it to `"*"`.
- **Backend hard-fail for multi-replica without a stable secret key.**
  `check_production_readiness()` now emits an `ERROR` (escalated from a
  `WARN`) when `SHOREGUARD_REPLICAS > 1` and `auth.secret_key` is unset,
  which causes `enforce_production_safety()` to raise a `RuntimeError`
  at startup. The original rate-limiter `WARN` stays because the
  in-process limit problem is orthogonal to the secret key one.
- **CI `helm-lint` job extended** to render the production preset and
  assert that the multi-replica-without-secretKey footgun guard
  actually fires.

### Fixed

- **Release workflow: `aquasecurity/trivy-action` pin** bumped from the
  non-existent `@0.28.0` tag to `@v0.35.0`. The old pin failed the
  GitHub Actions resolver *before* any step ran, so the `docker`
  job in the release workflow never reached `build-push-action` and
  the `v0.30.0` image never landed on GHCR (only `:latest` was
  available). Verified against the failed run of `v0.30.0`
  (`gh run view 24282878746 --log-failed` → `Unable to resolve
  action aquasecurity/trivy-action@0.28.0`). The action's maintainers
  migrated all tags to the `v`-prefix convention — `@v0.35.0` is the
  current stable tag and keeps the same `image-ref` / `exit-code` /
  `severity` / `vuln-type` input surface we rely on.

## [0.30.0] — 2026-04-11

The headline of this release is **federation in production shape**:
ShoreGuard now ships with a topbar switcher, label-based gateway
filtering, per-gateway audit attribution, and a single-file Python
script that drives the complete agent → routed inference → L7 denial
→ approve → audit → retry flow against **two** live OpenShell
clusters in parallel. The same release also closes the long-standing
"webhook backend exists, no UI" gap with a new `/webhooks` admin
page, and shore-up audit attribution across the gateway routes so the
audit log can be sliced by gateway with no cross-attribution leaks.

Two end-to-end automation scripts (`scripts/m7_demo.py` and
`scripts/m8_demo.py`) now exercise the full vision flow on every run;
both pass exit 0 in ~30 seconds and ~3-4 minutes respectively,
against real OpenShell gateways and a real Anthropic API key.

### Added

- **Webhook management UI at `/webhooks`** (admin only). Lists every
  registered webhook with channel badge, event-type chips, active /
  paused state, and per-row actions for test, view delivery log,
  pause/resume, edit, and delete. Inline create form with a one-time
  HMAC signing-secret reveal callout. Edit and delivery-log modals.
  The webhook backend has shipped for several releases — this is the
  first operator-facing surface for it.
- **Topbar gateway switcher**. The read-only gateway status badge
  has been replaced with a dropdown that lists every registered
  gateway with status dot and labels, and navigates to the picked
  gateway's detail page on click. Pure URL navigation, no client-side
  "active gateway" state. Available on every page.
- **Label filter on the gateways list page**. New text input next
  to the existing free-text filter accepts `key:value` (or
  comma-separated `k:v,k2:v2` for AND semantics) and reduces the
  table to gateways carrying those labels. The backend `?label=`
  query parameter on `/api/gateway/list` was already supported.
- **Audit log filterable by gateway**. New `?gateway=<name>` query
  parameter on `/api/audit` and `/api/audit/export`, plus a matching
  text input on the audit page. Lets an operator reconstruct the
  full register → configure → run → deny → approve sequence for one
  gateway in chronological order, even when other gateways are
  active concurrently.
- **Webhook CRUD now lands in the audit log**. New `webhook.create`,
  `webhook.update`, `webhook.delete`, and `webhook.test` audit
  entries carry the URL, event types, and channel type in the
  detail blob. This was the last unaudited route family in the API.
- **`WebhookService.fire_to()` direct delivery**. The webhook
  service now exposes a method to deliver an event to one specific
  active webhook, bypassing the subscription filter. The `/test`
  endpoint uses this so clicking the "Test" button on a webhook
  always reaches its target — even if the webhook doesn't subscribe
  to `webhook.test`. Paused webhooks now return HTTP 409 instead of
  silently dropping the request.
- **End-to-end demo scripts and runbooks.** `scripts/m7_demo.py`
  drives the single-gateway vision flow (login → register →
  inference provider → launch sandbox → claude agent → L7 denial →
  approve → audit → retry) in ~30 seconds. `scripts/m8_demo.py`
  does the federated version against two clusters in ~3-4 minutes,
  with per-gateway audit-attribution assertions. Each script ships
  alongside a markdown runbook (`scripts/m7-demo.md`,
  `scripts/m8-demo.md`) for the manual recipe. Both scripts are
  idempotent — re-running deletes any leftover state before
  recreating.

### Fixed

- **`GET /api/gateway/{name}/info` returned 500** on a connected
  gateway. `GatewayService.get_info()` injects `configured` and
  `version` into the response dict, but `GatewayResponse` was
  `extra="forbid"`, so the live endpoint crashed inside FastAPI's
  response validator. Schema now accepts both fields.
- **Gateway-route audit entries were landing with `gateway_name=NULL`.**
  `gateway.register`, `unregister`, `setting_update`/`delete`,
  `update_metadata`, `start` / `stop` / `restart` / `destroy` all
  pass `gateway=name` to `audit_log()` now, so the new
  `?gateway=<name>` filter actually finds them. Without this, every
  gateway-scoped audit row was invisible to per-gateway queries.
- **Webhook `/test` endpoint silently produced zero deliveries**
  when the target webhook didn't subscribe to `webhook.test` (or
  `*`). The global `fire()` path filters by subscription, so the
  test button was a lie unless the webhook happened to subscribe to
  the test event type. The new `fire_to()` direct-delivery path
  fixes it; paused webhooks now return 409 instead of dropping.
- **CSP-strict header tests** asserted `'unsafe-inline'` was not a
  substring of the CSP header, which broke after the v0.29 fix that
  added `style-src-attr 'unsafe-inline'` for Alpine.js's inline
  `style` attributes (x-show / x-cloak / x-transition). Replaced
  with a per-directive check that allows the narrower
  `style-src-attr` while keeping `default-src`, `script-src`, and
  `style-src` strict.

## [0.29.0] — 2026-04-11

This release closes **M1 OpenShell v0.0.26 Alignment**, **M2 OCSF
Observability**, **M3 L7 Denial Intelligence** (in the reduced form
documented in S3.1), and **M5 Production Readiness**. Highlights:
OpenShell v0.0.26 stub regeneration with TTY exec and named inference
routes, the full gateway settings API, effective-policy and provider-env
projection views, a policy-analysis submission endpoint, OCSF parsing
plus server-side filters in the sandbox logs viewer, denial context UX
on the approvals page, `/version` and hard-fail production checks,
backup/restore scripts, a rollback runbook, and Trivy + Bandit in CI.

### Added

- **OpenShell v0.0.26 alignment (M1 / S1.1).** Protobuf stubs regenerated
  against upstream OpenShell v0.0.26 (was v0.0.22). Three stub files
  actually changed (`inference_pb2.py`, `openshell_pb2.py`,
  `openshell_pb2.pyi`); the rest compiled byte-identically. This unblocks
  the two user-visible features below.
- **TTY exec for interactive commands.** `POST
  /api/gateways/{gw}/sandboxes/{name}/exec` now accepts a boolean `tty`
  field in the request body. When `true`, the gateway allocates a
  pseudo-terminal so interactive programs that check `isatty()` (e.g.
  `python` REPL, `vim`, `htop`) behave correctly. Defaults to `false`,
  so existing callers are unaffected. Requires a gateway running
  OpenShell v0.0.23 or newer.
- **Named inference routes on `GET /inference`.** `GET
  /api/gateways/{gw}/inference` now accepts an optional `?route_name=`
  query parameter. Empty (the default) returns the cluster's default
  inference route; passing a name like `sandbox-system` returns the
  route that OpenShell v0.0.25+ uses for sandbox system-level model
  calls. `PUT /inference` already accepted `route_name` in the request
  body; this release closes the GET-side gap.
- **Gateway Settings API (M1 / S1.2).** New admin-gated REST endpoints
  expose OpenShell's global gateway configuration:
  `GET /api/gateway/{name}/settings`,
  `PUT /api/gateway/{name}/settings/{key}` (body `{"value": …}` accepting
  string, bool, or int), and `DELETE /api/gateway/{name}/settings/{key}`.
  OpenShell has no separate `UpdateGatewayConfig` RPC; updates are sent
  per-key via the existing `UpdateConfig` RPC with the `global` flag set.
  The new API is value-agnostic — any settings key the gateway recognises
  (including the new `ocsf_logging_enabled` toggle) can be read and
  written without further code changes.
- **Effective policy view — `GET /sandboxes/{name}/policy/effective`
  (M1 / S1.3).** Stable contract endpoint for "what the gateway actually
  enforces", as opposed to "what was last PUT". Presets are merged
  eagerly into the declared policy today, so the endpoint returns the
  stored envelope with an added `source: "gateway_runtime"` marker,
  giving the UI a stable route even if OpenShell ever separates declared
  from effective server-side.
- **Provider env-var projection view — `GET /providers/{name}/env`
  (M1 / S1.3).** Read-only endpoint that returns the environment
  variables a provider injects into sandboxes — keys only, values
  always redacted. Each entry is tagged with `source`: `credential`,
  `config`, or `type_default` (from the provider type's `cred_key` in
  `openshell.yaml`). Useful for debugging agent misconfiguration without
  exposing secrets.
- **`POST /sandboxes/{name}/policy/analysis` (M1 / S1.3, closes M1).**
  Pass-through REST endpoint for the OpenShell `SubmitPolicyAnalysis`
  RPC. External denial analyzers (LLM-backed or rule-based) can submit
  observed denial summaries + proposed policy chunks through ShoreGuard's
  HTTP API; the gateway decides accept/reject per chunk and returns
  counters plus rejection reasons. Admin-only, rate-limited, audit-logged
  as `sandbox.policy.analyze`. This closes **M1 OpenShell v0.0.26
  Alignment** — S1.1, S1.2, and S1.3 are now all merged.
- **OCSF parsing & rendering in sandbox logs (M2 / S2.1).** OpenShell v0.0.26
  emits structured security events in an OCSF shorthand format over the
  existing `SandboxLogLine` stream (level `"OCSF"`, target `"ocsf"`).
  ShoreGuard now parses these lines via a new `shoreguard.services.ocsf`
  module and exposes `class_prefix`, `activity`, `severity`, `disposition`,
  `summary`, and both bracket + gRPC structured fields on every log entry
  that looks like OCSF. The sandbox logs viewer renders class badges,
  disposition colours (green = ALLOWED, red = DENIED/BLOCKED), dynamic
  class-prefix chips, and a per-row expand for structured field details.
  Live websocket stream and the REST `/sandboxes/{name}/logs` endpoint
  both include the parsed `ocsf` dict when present.
- **OCSF server-side filters on `GET /sandboxes/{name}/logs` (M2 / S2.2).**
  Four new query parameters — `ocsf_only`, `ocsf_class`, `ocsf_disposition`,
  `ocsf_severity` — let advanced consumers pull forensic-sized windows
  without client-side post-processing. The sandbox logs viewer exposes
  `ocsf_only` as a "Server OCSF" toggle next to the existing level filters.
- **Gateway observability toggle UI (M2 / S2.2).** The gateway detail page
  now includes an "Observability" fieldset with a form-switch bound to the
  upstream `ocsf_logging_enabled` gateway setting, wired via the existing
  `PUT /gateway/{name}/settings/{key}` endpoint.
- **Denial context UX on the approvals page (M3 / S3.1, closes M3
  reduced).** Re-verification of OpenShell v0.0.26/v0.0.27 protos
  (byte-identical) showed M3 was only "blocked" in the strictest
  `ListDenials` sense: three read paths already bring rich denial context
  into the control plane (`GetDraftPolicy`, `GetDraftHistory`, and the
  OCSF parser from S2.1). This sprint closes the remaining gaps:
  `_chunk_to_dict()` now forwards `denial_summary_ids`; the approvals
  table gains a "Seen" column formatting `first_seen` / `last_seen` /
  `hit_count`; the expand row surfaces `stage`, denial summary IDs as
  monospace chips, and a "View in logs" button. The logs viewer extracts
  a best-effort triggering binary and shows a "Find in approvals" button
  on DENIED/BLOCKED OCSF events that navigates via
  `#binary=X&host=Y` hash fragment; the approvals page listens to
  `sg:approvals-update` for live-refresh on draft_policy_update events.
  The history modal gains per-event-type filter chips with count badges,
  and the sandbox overview Approvals card now shows
  `security_flagged_count` and `last_analyzed_at_ms`. Closes M3 with the
  documented caveat that the full `DenialSummary` struct
  (`l7_request_samples`, `sample_cmdlines`, `ancestors`, `binary_sha256`)
  remains push-only in upstream v0.0.27 and stays a future feature
  request to NVIDIA/OpenShell.
- **`/version` endpoint (M5 / S5.1).** New unauthenticated endpoint that
  returns `{version, git_sha, build_time}` for the running binary, so
  operators can verify which artifact is serving traffic after a deploy.
  Build identity is propagated through new `SHOREGUARD_GIT_SHA` and
  `SHOREGUARD_BUILD_TIME` Dockerfile ARGs, `release.yml` build-args, and
  `shoreguard_info` Prometheus labels. A short-SHA image tag
  (`ghcr.io/.../shoreguard:a1b2c3d`) is now published alongside semver.
- **Hard-fail on production-readiness ERRORs (M5 / S5.1).** New
  `Settings.enforce_production_safety()` runs at startup and refuses to
  boot when `check_production_readiness()` reports any `ERROR:`-severity
  config issue (weak secret, CORS wildcard + credentials, SQLite in prod,
  strict CSP disabled, unrestricted self-registration in prod). Set
  `SHOREGUARD_ALLOW_UNSAFE_CONFIG=true` to downgrade the error to a
  `CRITICAL` log line — documented as an emergency override in
  [reference/configuration.md](reference/configuration.md).
- **Backup and restore scripts (M5 / S5.1).** New `scripts/backup.py`
  and `scripts/restore.py` auto-detect SQLite vs Postgres from the
  database URL. SQLite uses the built-in online backup API; Postgres
  shells out to `pg_dump --format=custom` / `pg_restore --clean
  --if-exists`. The [Database Migrations](admin/database-migrations.md)
  guide now recommends these scripts as the primary backup path.
- **Rollback runbook (M5 / S5.2).** New
  [admin/rollback.md](admin/rollback.md) consolidates the
  incident-response flow (symptom detection → image rollback → optional
  DB rollback or restore → verification → post-mortem) into one page,
  with links into existing troubleshooting, migration, and deployment
  docs.
- **Supply-chain hardening (M5 / S5.3, closes M5).** CI gains a new
  `security` job that runs Bandit at medium-and-above severity over the
  `shoreguard` package (pip-audit already covers dependency CVEs; Bandit
  adds source-level SAST for Python-specific patterns — eval, shell
  injection, insecure hashing). The release pipeline runs Trivy against
  the freshly-built image by digest between build-push and cosign, with
  `ignore-unfixed=true` and failure on CRITICAL/HIGH only. Grafana
  starter dashboard at `deploy/grafana/shoreguard.json` covers six
  panels — HTTP request rate by status, p95/p99 latency by path,
  gateways by status, operations in flight, webhook success rate — with
  a `shoreguard_info` build annotation track so deploys show up as
  vertical lines across every panel.

### Changed

- **Error responses now follow RFC 9457 Problem Details.** Error bodies
  are served with `Content-Type: application/problem+json` and carry the
  standard `type`, `title`, `status`, and `detail` fields alongside the
  existing ShoreGuard `code` (and any extension members such as
  `request_id`, `errors`, `feature`, or `upgrade_required`). The
  `detail` field is unchanged, so existing clients that read only
  `body.detail` (including the ShoreGuard web UI) keep working without
  modification.
- **`PolicyManager.watch()` stream flattener forwards `target` and
  `fields`.** The live `WatchSandbox` consumer was dropping both on the
  live pathway, even though `get_logs()` surfaced them correctly via the
  unary RPC. Additive change — no existing consumer breaks.
- **Sync `OperationService` removed; tests run against
  `AsyncOperationService` directly.** Production has used
  `AsyncOperationService` exclusively since v0.27; the sync class
  remained only because tests reached it through an
  `_AsyncOperationAdapter` shim in `conftest.py`. This release deletes
  the sync class (~480 LOC), the adapter, and the sync-class test file
  entirely. The new harness runs `AsyncOperationService` on an
  in-memory `aiosqlite` engine with in-flight LRO task drainage on
  teardown to avoid closed-DB races. Net: **-2077 LOC**, full suite
  2477 passed, 35 skipped.

### Fixed

- **`verify_password` no longer raises on corrupt hashes.** A malformed
  or truncated password hash row in the database used to surface as an
  unhandled `PwdlibError`; it now returns `False` so the login attempt
  fails cleanly and the account lockout counter advances as intended.
- **`min_level` parameter on `GET /sandboxes/{name}/logs` now preserves
  OCSF events.** OpenShell's `level_matches()` helper assigns unknown
  levels (including `"OCSF"`) numeric rank 5, which any non-empty
  `min_level` silently dropped. ShoreGuard now always fetches upstream
  with `min_level=""` and applies the level filter locally, bypassing
  OCSF entries unconditionally.
- **`check_production_readiness()` now actually returns the warnings list**
  that its type signature promised — the method previously collected the
  list and fell through without a `return` statement, so callers always
  got `None`.

### Tests

- **Auth edge-case coverage raised from 75% to 96%** — targeted tests
  for token expiry, account lockout transitions, and OIDC error paths.
- **WebSocket auth-error coverage raised from 67% to 94%** — coverage
  for authentication failure branches in the sandbox log stream
  endpoint.
- **`shoreguard/services/operations.py` coverage raised from 61% to
  100%.** The previous baseline reflected an inverted gap: the test
  suite exercised the sync `OperationService` via an adapter in
  `conftest.py` while the prod `AsyncOperationService` (used by the API
  routes) was untested. The follow-up refactor in this release
  consolidates the two classes into one, so this coverage win is now
  permanent.

## [0.27.0] — 2026-04-10

### Security

- **Strict CSP is now the default** — `SHOREGUARD_CSP_STRICT` defaults to
  `true`, closing the loop on the M1–M4 hardening work that shipped in
  v0.26.0 plus the M2.1 inline-event-handler extraction completed in this
  release. Fresh installs now receive a Content-Security-Policy with a
  per-request cryptographic nonce on every `<script>` tag, **no**
  `'unsafe-inline'`, `frame-ancestors 'none'` (clickjacking protection),
  `base-uri 'self'` (base-tag injection protection), and
  `form-action 'self'` (form hijacking protection). `'unsafe-eval'` is
  retained in `script-src` because Alpine.js uses the `Function()`
  constructor internally — the `@alpinejs/csp` build was evaluated during
  M2.1 but its expression parser is limited to plain property chains (no
  operators, no literals, no method-call arguments), which proved too
  restrictive for this UI. Unlike `'unsafe-inline'`, `'unsafe-eval'` does
  not permit DOM-injected script execution, so the XSS surface remains
  dramatically smaller than the legacy policy.

- **CSP hardening M2.1 — inline event handler extraction.** M2 in v0.26.0
  extracted inline `<script>` blocks but missed 28 inline event handler
  attributes (`onclick=""`, `onkeydown=""`) which `script-src-attr`
  blocks regardless of nonce. All 28 are now converted: static template
  handlers become Alpine `@click`/`@keydown` directives on registered
  `Alpine.data()` components, and dynamically-rendered `innerHTML`
  handlers use `data-action`/`data-arg` markers dispatched by a single
  delegated click listener per component. Inline `style=""` attributes
  produced by JS renderers (policy editor, wizard) also replaced with
  Bootstrap utility classes so `style-src-attr` stays clean.

  **For operators running stock ShoreGuard:** no action needed. The
  pages you already use (dashboard, sandboxes, wizard, policy editor,
  audit log, approvals, gateways, providers, users, groups, settings,
  invite flow) have all been refactored to work under strict CSP.

  **For operators with custom templates, inline scripts, or third-party
  embeds** that cannot yet be nonce-gated: set
  `SHOREGUARD_CSP_STRICT=false` to fall back to the legacy
  `'unsafe-inline'` policy. The legacy field `SHOREGUARD_CSP_POLICY`
  continues to work as an escape hatch when strict mode is off.

### Changed

- **Production-readiness check is now strict-mode aware.** The warning
  about `'unsafe-*'` directives in `auth.csp_policy` is now gated on
  `csp_strict=False` — when strict mode is enabled (default), that
  field is unused and no warning fires.

## [0.26.1] — 2026-04-10

### Changed

- **Docstring coverage — pydoclint clean across `shoreguard/`** — Every
  public function, method, and Pydantic model in `shoreguard/` now has a
  Google-style docstring with `Args`/`Returns`/`Raises`/`Attributes`
  sections as appropriate. 410 pre-existing pydoclint violations across
  21 files were fixed (96 in `api/schemas.py`, 64 in `services/operations.py`,
  51 in `api/pages.py`, …). Zero runtime behaviour changes; this unblocks
  `pydoclint` in CI so future docstring drift gets caught at review time.
- **Removed stale linter suppressions** — Systematic audit of all `# noqa` /
  `# type: ignore` / `# pyright: ignore` comments. ~150 justified suppressions
  kept (stdlib API signatures, SQLAlchemy column semantics, protobuf stub
  typing, fake gRPC test doubles, singleton `PLW0603`, `__init__` `D107`, …) —
  each now carries a comment explaining *why*. 12 non-justified suppressions
  removed by adding proper types or narrowing: SQLAlchemy event-handler
  params in `db.py`, `operation_service` / `gateway_service` narrowing in
  `api/main.py` + `api/metrics.py`, `_get_auth_settings` / `_webhook_settings`
  / `_cli_init_db` return types, `_UNSET` sentinel `cast()` in
  `services/registry.py` + `sandbox_meta.py`. The cleanup surfaced **two real
  type bugs**: `_cli_init_db` was annotated `-> None` despite callers invoking
  `.dispose()` on the returned `Engine`, and the module-level
  `operation_service` carried a stale `AsyncOperationService | OperationService
  | None` union that never matched runtime reality. Both fixed; no runtime
  behaviour change.

## [0.26.0] — 2026-04-09

### Added

- **CSP strict-mode foundation** — `SHOREGUARD_CSP_STRICT=true` opt-in
  enables a per-request nonce on `request.state.csp_nonce` and an
  unsafe-*-free Content-Security-Policy built from `auth.csp_policy_strict`
  (default remains off until the frontend refactor lands). Templates can
  reference `{{ csp_nonce(request) }}` on inline `<script>` tags and switch
  between the standard and CSP-safe Alpine.js builds via
  `{% if csp_strict_enabled() %}`. This is Milestone 1 of the multi-session
  CSP hardening plan — see `csp-hardening-followup.md` for the full roadmap.

### Changed

- **CSP hardening M2** — All inline `<script>` blocks extracted from Jinja
  templates into `frontend/js/` (`theme-init.js`, `dashboard.js`, `audit.js`;
  `providers.js` and `wizard.js` bind their own `DOMContentLoaded` handlers).
  `GW` is now read from `document.documentElement.dataset.gateway` in
  `constants.js`, eliminating the last Jinja-templated inline script. With
  `SHOREGUARD_CSP_STRICT=true`, strict CSP no longer reports inline-script
  violations — only inline-style (M3) and Alpine `x-data` (M4) violations
  remain.
- **CSP hardening M3** — All inline `style="..."` attributes and `<style>`
  blocks removed from Jinja templates. Shared patterns moved to the new
  `frontend/css/utilities.css` (sg-prefixed width/max-width/font-size/cursor
  utilities) and auth pages share `frontend/css/auth.css`. Wizard step
  toggling now uses `classList.toggle('d-none', ...)` instead of
  `element.style.display`. With `SHOREGUARD_CSP_STRICT=true`, strict CSP no
  longer reports `style-src` violations — only Alpine `x-data` (M4) remains.
- **CSP hardening M4** — Every Alpine.js component is now registered via
  `Alpine.data(name, factory)` (per-file inside each `frontend/js/*.js`
  factory file, plus a new `frontend/js/auth.js` for the
  login/register/setup/invite forms). Templates reference them by name
  (`x-data="loginForm"`) instead of inline object or spread-merge literals —
  the four `{ ...pageFn(), ...sortableTable(...) }` patterns on the
  gateways/policies/users/groups pages are now `gatewaysList`,
  `presetsListPage`, `usersListPage`, `groupsListPage`. Directive expressions
  containing arrow functions, `if` statements, or multi-statement sequences
  (logout click, toast auto-remove, ws-state listener, clipboard-copy
  buttons, inference-config `x-effect`, filesystem-policy add-form focus)
  were extracted to store/component methods (`$store.auth.logout`,
  `$store.toasts.scheduleRemove`, `onWsState`, `copyInvite`, `copyKey`,
  `maybeLoad`, `openAddForm`). Auth pages now share the new
  `components/alpine_loader.html` partial with the main base template so the
  CSP build is loaded consistently. With `SHOREGUARD_CSP_STRICT=true`, the
  application loads with zero CSP-related Alpine violations — clearing the
  last blocker to making strict CSP the default in a future minor bump.
- **Pyright on `tests/` + parallel test execution** — Pyright's include list
  now covers `tests/` alongside `shoreguard/`, and `pytest-xdist` is a dev
  dependency so the suite runs with `pytest -n auto`. Enabling pyright on
  tests surfaced 303 pre-existing errors across 19 files (Optional
  narrowing, fake gRPC stub assignments typed as `OpenShellStub`, protobuf
  enum kwargs passed as raw ints, and a handful of test-setup bugs such as
  `_FakeRpcError` missing `cancel()`). All fixed test-side — zero changes
  to `shoreguard/` — via `assert x is not None` narrowing and narrow
  `# type: ignore[assignment|arg-type|override]` comments where the fake
  object pattern made narrowing impossible. On a 16-core box the suite
  now runs in ~43s parallel instead of ~4:46 serial (6.6× speedup).

## [0.25.0] — 2026-04-09

### Added

- **`shoreguard config show [section]`** — dump the effective configuration
  as a table, JSON, or `.env`-style output. Secret values (`secret_key`,
  `admin_password`, `client_secret`, `password`) are redacted by default;
  `--show-sensitive` reveals them.
- **`shoreguard config schema [section]`** — dump pristine defaults plus
  descriptions in table/json/env/markdown format. Used to regenerate
  `docs/reference/settings.md`.
- **Self-documenting settings** — every `Settings` field now carries
  `Field(default=..., description=...)`. All ~100 environment variables
  have a one-line description surfaced via `config show`.
- **`shoreguard audit export`** — offline audit log export (JSON or CSV)
  with a `sha256sum`-compatible digest file and a `manifest.json` carrying
  entry count, filters, timestamp, and tool version. All three files are
  written with 0600 permissions.
- **Structured logging improvements** — text mode now renders
  `[request_id]` via the `RequestIdFilter` (was silently dropped);
  `JSONFormatter` adds `module`/`func`/`line`, merges caller extras, and
  emits `stack_info`. uvicorn access logs carry the same request-id as
  application logs in both modes.
- **Global per-IP rate limiter** (`SHOREGUARD_GLOBAL_RATE_LIMIT_*`) as a
  coarse DDoS guardrail applied by `global_rate_limit_middleware` to every
  HTTP request except health/metrics endpoints.
- **Request body size limit** middleware
  (`SHOREGUARD_LIMIT_MAX_REQUEST_BODY_BYTES`, default 10 MiB) returning
  HTTP 413 before Starlette reads the body.
- **DB migration retry loop** on startup with exponential backoff against
  `OperationalError` (`SHOREGUARD_DB_STARTUP_RETRY_*`). Compose-friendly.
- **Background task supervision** surfaced in `/readyz` with
  `asyncio.wait_for` on dependency probes (`SHOREGUARD_READYZ_TIMEOUT`).
- **Production-readiness check expansion** — six new warnings: HSTS off,
  CSP contains `unsafe-*`, `allow_registration` in prod, multi-replica
  with in-process rate limiter, SQLite in prod, text log format in prod.
  Warnings now carry `ERROR:` / `WARN:` severity prefixes.
- **`docs/reference/settings.md`** — auto-generated reference of every
  `SHOREGUARD_*` environment variable grouped by sub-model.

### Changed

- **Audit log is now ORM-level append-only.** `AuditEntry` rows cannot be
  updated via the ORM, and deletion is only permitted from
  `AuditService.cleanup()` via a `ContextVar`-gated bypass. Enforcement
  raises `AuditIntegrityError` on commit. `cleanup()` switched to
  row-by-row deletion so the `before_delete` listener fires. Direct SQL
  still bypasses enforcement — DB-level triggers are a post-v1.0 item.
- **CLI callback respects `ctx.invoked_subcommand`** — the main Typer
  callback no longer tries to bind a socket when `shoreguard config ...`
  or `shoreguard audit ...` subcommands are invoked.
- **Graceful shutdown timeout** honoured by uvicorn startup path.
- **CORS settings** tightened and exposed via `SHOREGUARD_CORS_*`.

### Security

- **OIDC SSRF protection** — `discover()`, `get_jwks()`, and
  `exchange_code()` run all URLs (including those returned by a
  provider's discovery document) through the existing private-IP check.
  A compromised identity provider can no longer pivot requests to
  internal services like cloud metadata endpoints.

### Fixed

- **Version drift** — `pyproject.toml` was still reporting `0.23.0` after
  the v0.24.0 tag was cut. This release bumps directly to 0.25.0 to
  resync the package metadata with the release stream.

## [0.24.0] — 2026-04-08

### Added

- **1,193 mutation-killing tests** — targeted tests designed to eliminate
  survived mutants identified by mutmut v3.5. Test count: 1,175 → 2,368.
  - New `test_openshell_meta.py` — first-ever coverage for OpenShell metadata
    loader (27 mutants, previously 100% survival).
  - New `test_auth_mutations.py` (194 tests) — exhaustive auth CRUD, RBAC
    role resolution, service principal lifecycle, group management, session
    tokens, gateway-scoped roles.
  - Extended 20 existing test files across all major modules: formatters,
    sandbox templates, routes, OIDC, local gateway, webhooks, gateway service,
    operations, registry, policy, all client modules, DB, presets, CLI import,
    and audit service.

### Fixed

- **Pyright strict mode** — resolved all 30 type-check errors (0 remaining):
  - `operation_service` union type corrected for async/sync variants.
  - `_get_svc()` return type narrowed to `AsyncOperationService` in route
    handlers (`routes/operations.py`, `lro.py`).
  - `db_cfg` possibly-unbound variable in `db.py` PostgreSQL branch.
  - `discover()` return type in `api/oidc.py`.
  - `update_group` sentinel parameter type in `api/auth.py`.
  - Async/sync union narrowing in `main.py`, `metrics.py`,
    `routes/gateway.py`, `routes/sandboxes.py`.

## [0.23.0] — 2026-04-08

### Added

- **OIDC/SSO authentication** — multi-provider support with callback flow,
  role mapping, and state validation (`api/oidc.py`,
  `alembic/versions/012_oidc_fields.py`).
- **SSRF validation** — URL allowlist/blocklist for webhook targets prevents
  server-side request forgery via internal addresses.
- **Input sanitization** — centralized validators for names, URLs, certs,
  env vars, and command strings with configurable limits via
  `SHOREGUARD_LIMIT_*` env vars.
- **pip-audit in CI** — automated dependency vulnerability scanning in the
  GitHub Actions workflow.
- **Deep health checks** — `/readyz` now measures DB latency, reports gateway
  health summary (total/connected/degraded), supports `?verbose=true` for
  per-gateway details.
- **PostgreSQL connection pooling** — `DatabaseSettings` with `pool_size`,
  `max_overflow`, `pool_recycle`, `statement_timeout_ms` via
  `SHOREGUARD_DB_*` env vars.
- **Graceful shutdown** — LRO task cancellation (`shutdown_lros()`), webhook
  delivery task tracking with `shutdown()`, ordered resource disposal.
- **Async engine disposal** — `dispose_async_engine()` for clean DB shutdown.
- **Docs** — OIDC guide, security concepts, troubleshooting, audit guide,
  webhooks guide, Prometheus integration, gateway roles admin.
- **108+ new tests** — OIDC, input validation, SSRF, webhook secret leak.
  Total: ~1194.

### Changed

- **Typed API response models** — `extra="forbid"` on Category-A models
  prevents uncontrolled field leakage through `extra="allow"`.
- **Webhook HMAC secret** no longer exposed on GET/LIST endpoints — only
  returned on create (`WebhookCreateResponse`).
- **Docs restructured** — `guide/` → `guides/`, new `concepts/` and
  `integrations/` directories.
- **`graceful_shutdown_timeout`** default raised from 5 → 15 seconds.

### Security

- Fixed webhook HMAC signing secret leak on all GET/PUT responses.
- SSRF protection for webhook target URLs.
- Input length/format validation on all mutation endpoints.

## [0.22.0] — 2026-04-08

### Added

- **User groups / teams** — named collections of users for group-based RBAC.
  Groups have a global role and optional per-gateway role overrides, mirroring
  the existing individual user role system.
- **Group membership management** — add/remove users to groups via API and
  frontend UI (`/groups` page with member modal).
- **Group gateway-scoped roles** — per-gateway role overrides for groups, reusing
  the gateway roles modal from user/SP management.
- **4-tier role resolution** — individual gateway > group gateway > individual
  global > group global. When a user belongs to multiple groups the highest rank
  wins.
- **Group audit trail** — `group.create`, `group.update`, `group.delete`,
  `group.member.add`, `group.member.remove`, `group.gateway_role.set`,
  `group.gateway_role.remove` actions logged.
- **65 new tests** — CRUD, membership, cascade deletes, role resolution priority
  chain, and HTTP-level endpoint tests (`test_group_rbac.py`). Total: 1086.

### Changed

- **Gateway roles modal** — now supports `user`, `sp`, and `group` entity types.

## [0.21.0] — 2026-04-07

### Added

- **Rate limiting** — per-IP sliding-window rate limiter (`api/ratelimit.py`)
  with configurable limits via `SHOREGUARD_RATELIMIT_*` env vars.
- **Account lockout** — progressive lockout after failed login attempts
  (`api/auth.py`) with configurable thresholds.
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`,
  `Strict-Transport-Security`, etc. via middleware (`api/security_headers.py`).
- **Password strength validation** — `api/password.py` with length, complexity,
  and common-password checks.
- **Structured error codes** — machine-readable `code` field (e.g.
  `GATEWAY_NOT_FOUND`, `RATE_LIMITED`) in all error responses
  (`api/error_codes.py`, `api/errors.py`).
- **WebSocket server heartbeat** — periodic `{"type": "heartbeat"}` messages
  during idle with `dropped_events` counter for backpressure visibility.
- **WebSocket backpressure disconnect** — slow consumers disconnected after
  configurable consecutive drop limit (`SHOREGUARD_WS_BACKPRESSURE_DROP_LIMIT`).
- **WebSocket client reconnect hardening** — heartbeat watchdog (45 s timeout),
  max retry limit (20), exponential backoff, and `sg:ws-state` events for
  connection state UI indicator.
- **Prometheus metrics** — `/metrics` endpoint with login and rate-limit
  counters.

### Changed

- **Dynamic `__version__`** — `shoreguard/__init__.py` now reads version from
  package metadata (`importlib.metadata`) instead of hardcoded string; single
  source of truth in `pyproject.toml`.
- **Deploy configs** — consolidated Caddyfile and standalone compose into
  `deploy/` directory.
- **.gitignore** — trimmed from ~200 to ~30 lines, removed stale entries.

## [0.20.0] — 2026-04-07

### Added

- **Pydantic Settings** — centralized `shoreguard/settings.py` with 11
  nested sub-models replacing 11 `os.environ.get()` reads and 60+
  hardcoded constants.  All tuneable via `SHOREGUARD_*` env vars
  (e.g. `SHOREGUARD_GATEWAY_BACKOFF_MIN`, `SHOREGUARD_OPS_RUNNING_TTL`).
- **Pydantic response models** — typed response schemas (`schemas.py`)
  on all API endpoints with OpenAPI tag metadata.
- **Request-ID tracking** — `X-Request-ID` header propagated through
  middleware, available in all log records via `%(request_id)s`.
- **Prometheus latency metrics** —
  `shoreguard_request_duration_seconds` histogram with method/path/status
  labels, plus `/metrics` endpoint.
- **Structured JSON logging** — `SHOREGUARD_LOG_FORMAT=json` for
  machine-readable log output.
- **GZip compression** — responses ≥ 1 KB automatically compressed
  via Starlette GZip middleware.
- **Audit pagination** — `GET /api/audit` supports `offset`/`limit`
  with `items`/`total` response format.
- **Input validation module** — `api/validation.py` with reusable
  description, label, and gateway-name validators.
- **DB-backed operations** — `AsyncOperationService` with SQLAlchemy
  async, orphan recovery, and configurable retention.
- **SSE streaming for LROs** — `GET /api/operations/{id}/stream`
  streams real-time status/progress updates via Server-Sent Events.
- **`run_lro` helper** — `api/lro.py` with idempotency-key support,
  automatic 202 response, and background task lifecycle.
- **Async DB layer** — `init_async_db()` /
  `get_async_session_factory()` in `db.py` for aiosqlite-backed async
  sessions.
- **Performance indexes** — migrations 008–010 adding indexes on audit
  timestamp, webhook delivery, and operation status.
- **Gateway register page** — `/gateways/new` with breadcrumb
  navigation, description and labels fields (replaces modal).
- **Provider create/edit pages** — `/gateways/{gw}/providers/new` and
  `.../providers/{name}/edit` with Alpine.js `providerForm()` component
  (replaces modal).

### Changed

- **Consistent pagination** — all list endpoints return
  `{"items": [...], "total": N}` format.
- **CLI env-var hack removed** — `cli.py` no longer writes
  `os.environ["SHOREGUARD_*"]`; uses `override_settings()` instead.
- **Frontend modals→pages** — gateway registration and provider
  create/edit modals replaced with dedicated page routes and breadcrumb
  navigation.

### Removed

- **In-memory LRO store** — replaced by DB-backed
  `AsyncOperationService`.
- **Hardcoded constants** — `_BACKOFF_MIN`, `_MAX_RESULT_BYTES`,
  `DELIVERY_TIMEOUT`, `MAX_DESCRIPTION_LEN`, etc. now read from
  Settings.
- **Gateway/provider modals** — `#registerGatewayModal` and
  `#createProviderModal` removed from frontend templates.

### Dependencies

- Added `pydantic-settings>=2.0`.

## [0.19.0] — 2026-04-07

### Added

- **Async sandbox exec** — `POST /sandboxes/{name}/exec` now returns a
  long-running operation (LRO) with polling pattern instead of blocking.
- **Exec audit fields** — `command`, `exit_code`, and `status` added to
  `sandbox.exec` audit detail for full traceability.
- **mTLS auto-generation** — `openshell-client-tls` secret with CA cert
  is automatically created for OpenShell gateway connections.
- **Docker Compose profiles** — optional `paperclip` profile for
  Paperclip integration alongside ShoreGuard.
- **Caddy reverse proxy** — new Caddy service and OpenClaw profile in
  the deploy stack for production-ready TLS termination.
- **Hardened OpenClaw sandbox** — dedicated sandbox image with security
  documentation and deployment via generic ShoreGuard APIs.
- **Deploy stack README** — ecosystem section and deploy stack overview
  added to the project README.

### Fixed

- **gRPC exec timeout** — default timeout raised to 600 s for
  long-running agent sessions.
- **SetClusterInference** — `no_verify` flag now correctly set in the
  gRPC request.
- **LOCAL_MODE endpoints** — private IP addresses are now accepted when
  registering gateways in local mode.
- **Gateway context** — switched from `ContextVar` to `request.state`
  to avoid cross-request leaks.
- **openshell-client-tls** — secret now includes the CA certificate for
  proper chain verification.
- **sandbox_meta_store import** — resolved binding issue that caused
  startup failures.
- **Exec tests** — aligned with async LRO pattern and added shlex
  validation before returning 202.

### Changed

- **README** — redesigned with updated architecture diagram and sandbox
  vision narrative.
- **Architecture diagram** — added multi-gateway topology, observability
  components, unified operators, agent platform UIs, and plugins.
- **Mermaid diagrams** — improved contrast for dark-mode rendering.

### Docs

- Deploy guide expanded with profiles and Paperclip integration steps.
- Plugin install command updated to `@shoreguard/paperclip-plugin` from
  npm.
- Discord reference removed from OpenClaw README.

## [0.18.1] — 2026-04-06

### Added

- **Sandbox metadata UI** — labels and description are now visible and
  editable across the entire frontend:
  - **Detail page**: Metadata fieldset with description input, label
    badges (add/remove), and Save button (PATCH, operator role).
  - **Wizard**: Description and labels fields in Step 2 (Configuration),
    shown in Step 4 summary, included in create payload.
  - **List page**: Description column (truncated) and label badges
    inline under sandbox name.

## [0.18.0] — 2026-04-05

### Added

- **Sandbox labels & description** — sandboxes now support `labels`
  (key-value pairs) and `description` metadata, stored in ShoreGuard's
  DB (OpenShell is unaware). New `sandbox_meta` table with per-gateway
  scoping.
- **`PATCH /sandboxes/{name}`** — update labels and/or description on
  existing sandboxes (requires operator role).
- **Label filtering** — `GET /sandboxes?label=key:value` filters
  sandboxes by labels (AND-combined, same semantics as gateway list).
- **Alembic migration 007** — creates `sandbox_meta` table with
  `(gateway_name, sandbox_name)` unique constraint.

## [0.17.0] — 2026-04-05

### Fixed

- **Exception handling** — narrowed overly broad `except Exception` blocks in
  health check logging, webhook delivery, reconnection loop, and operation
  lifecycle. All handlers now log with full traceback and re-raise or return
  safe error responses.
- **SP expiry timezone** — `expires_at` comparison in `_lookup_sp_identity`
  now correctly handles naive datetimes by normalising to UTC before comparison.
- **Bootstrap admin** — `bootstrap_admin_user()` no longer raises on duplicate
  email when called during startup with an existing database.

### Changed

- **Logging consistency** — webhook delivery success/failure, gateway
  reconnection attempts, and operation lifecycle transitions now log at
  appropriate levels (INFO for business events, WARNING for recoverable
  errors, DEBUG for technical details).
- **Docstrings** — all public functions and classes pass `pydoclint` with
  strict Google-style checking (raises, return types, class attributes).
- **Type hints** — `require_role` return type corrected. Zero `pyright`
  errors on standard mode.
- **CI** — Python 3.14 target for CI matrix, ruff, and pyright.
  Bumped `docker/setup-buildx-action` to v4, `docker/build-push-action`
  to v7, `astral-sh/setup-uv` to v7.

### Added (tests only)

- **Webhook route tests** — 24 integration tests covering CRUD, validation,
  role enforcement (admin/viewer/unauthenticated), and service-not-initialised.
- **Error-case tests** — 13 tests across approvals (4), policies (3),
  providers (4), and sandboxes (2) for 404/409 error paths.
- **Template tests** — 9 tests for `sandbox_templates.py` (list, get, path
  traversal protection) and template route handlers.
- **Webhook delivery tests** — 13 tests for delivery records, cleanup,
  email channel dispatch, and the `fire_webhook` convenience function.
- **Auth endpoint tests** — 31 tests for `pages.py` covering setup wizard,
  login validation, user CRUD, gateway role management, self-registration,
  and service principal management error paths.
- **Total**: 915 tests (+86 from 0.16.2), coverage 82% → 84%.

## [0.16.0] — 2026-04-04

### Added

- **Webhook delivery log** — new `webhook_deliveries` table tracks every
  delivery attempt with status, response code, error message, and timestamps.
  Query via `GET /api/webhooks/{id}/deliveries`.
- **Webhook retry with exponential backoff** — HTTP 5xx and network errors
  trigger up to 3 retries (5s → 30s → 120s). Client 4xx errors fail immediately.
- **New webhook events** — `gateway.registered`, `gateway.unregistered`,
  `inference.updated`, `policy.updated` fire automatically after the
  corresponding API actions.
- **Enriched sandbox.created payload** — now includes `image`, `gpu`, and
  `providers` fields from the creation request.
- **API-key rotation** — `POST /api/auth/service-principals/{id}/rotate`
  generates a new key and immediately invalidates the old one (admin only).
- **API-key expiry** — optional `expires_at` timestamp on service principals.
  Expired keys are rejected at auth time.
- **API-key prefix** — new keys are prefixed with `sg_` and the first 12
  characters are stored as `key_prefix` for identification without exposing
  the full key. Legacy keys remain functional.
- **Sandbox templates** — YAML-based full-stack templates (`data-science`,
  `web-dev`, `secure-coding`) that pre-configure image, GPU, providers,
  environment variables, and policy presets. Available via
  `GET /api/sandbox-templates` and integrated into the wizard.
- **Alembic migration 005** — adds `webhook_deliveries` table.
- **Alembic migration 006** — adds `key_prefix` and `expires_at` columns
  to `service_principals` table.

### Changed

- **Webhook service** — `fire()` now creates delivery records per target
  before dispatching. `_deliver_http` replaced by `_deliver_http_with_retry`
  with retry logic.
- **Service principal creation** — keys now use `sg_` prefix format.
  `list_service_principals()` returns `key_prefix` and `expires_at` fields.
- **Users UI** — SP table shows key prefix, expiry badge (green/yellow/red),
  and rotate button. SP creation form includes optional expiry date.
- **Wizard UI** — step 1 shows sandbox template cards above community
  sandboxes. Selecting a template pre-fills all fields and jumps to summary.
  "Customize" button navigates back to configuration step.
- **Formatters** — `_EVENT_LABELS`, `_SLACK_COLORS`, `_DISCORD_COLORS`
  extended for 4 new events. `_payload_fields()` extracts provider, model,
  image, and endpoint fields.
- **Cleanup loop** — webhook delivery records older than 7 days are purged
  alongside operations and audit entries.
- **Documentation** — API reference updated with sandbox templates, delivery
  log, rotate endpoint, and new event types. Service principals guide expanded
  with key rotation, expiry, and prefix sections. Sandbox guide includes
  templates section with wizard integration.

## [0.15.0] — 2026-04-04

### Added

- **Gateway description** — free-text `description` field on gateways for
  documenting purpose and context (e.g. "Production EU-West for ML team").
- **Gateway labels** — key-value labels (`env=prod`, `team=ml`, `region=eu-west`)
  stored as `labels_json` column. Kubernetes-style key validation, max 20 labels
  per gateway, values up to 253 chars.
- **`PATCH /api/gateway/{name}`** — new endpoint to update gateway description
  and/or labels after registration (admin only). Supports partial updates via
  Pydantic `model_fields_set`.
- **Label filtering** — `GET /api/gateway/list?label=env:prod&label=team:ml`
  filters gateways by labels (AND semantics).
- **Alembic migration 004** — adds `description` (Text) and `labels_json` (Text)
  columns to the `gateways` table.

### Changed

- **Gateway list UI** — new description column (hidden on small screens) and
  label badges displayed below gateway names.
- **Gateway detail UI** — description and labels shown in details card with
  inline edit form (admin only).
- **Gateway registration modal** — new description textarea and labels textarea
  (one `key=value` per line).
- **`GatewayRegistry`** — `register()`, `_to_dict()`, and `list_all()` extended
  for description, labels, and label filtering. New `update_gateway_metadata()`
  method with sentinel-based partial updates.

## [0.14.0] — 2026-04-04

### Added

- **Notification channels** — webhooks now support `channel_type` field with
  values `generic` (default, HMAC-signed), `slack` (Block Kit formatting),
  `discord` (embed formatting), and `email` (SMTP delivery). Alembic migration
  003 adds `channel_type` and `extra_config` columns to the `webhooks` table.
- **Message formatters** — `shoreguard/services/formatters.py` with
  channel-specific formatting: Slack Block Kit with mrkdwn and color coding,
  Discord embeds with color-coded fields, plain-text email bodies.
- **Prometheus `/metrics` endpoint** — unauthenticated, exposes
  `shoreguard_info`, `shoreguard_gateways_total` (by status),
  `shoreguard_operations_total` (by status),
  `shoreguard_webhook_deliveries_total` (success/failed),
  and `shoreguard_http_requests_total` (by method and status code).
- **HTTP request counting middleware** — counts all API requests by method
  and status code for Prometheus.
- **`OperationStore.status_counts()`** — thread-safe method returning
  operation counts grouped by status.

### Changed

- **`WebhookService`** refactored for channel-type-aware delivery: `_deliver`
  dispatches to `_deliver_http` (generic/slack/discord) or `_deliver_email`.
  HMAC signature only applied for `generic` channel type.
- **Webhook API routes** accept `channel_type` and `extra_config` in create
  and update requests. Email channel requires `smtp_host` and `to_addrs`
  in `extra_config`.
- **Webhook API docs expanded** — channel types table, email `extra_config`
  example, corrected event types, Prometheus metrics table with scrape config.
- **Deployment docs** — new monitoring section with Prometheus scrape config.
- **README** — notifications and Prometheus metrics in features list and roadmap.
- Version bumped to `0.14.0`.
- 791 tests (up from 770).

### Fixed

- **`deps.py` type safety** — `get_client()`, `set_client()`, and
  `reset_backoff()` now raise `HTTPException(500)` when called without a
  gateway context instead of passing `None` to the gateway service. Fixes
  3 pre-existing pyright `reportArgumentType` errors.

### Dependencies

- Added `prometheus_client>=0.21`.
- Added `aiosmtplib>=3.0`.

## [0.13.0] — 2026-04-04

### Added

- **Docker deployment polish** — OCI image labels in `Dockerfile`, restart
  policies, dedicated `shoreguard-net` bridge network, configurable port and
  log level, and resource limits in `docker-compose.yml`.
- **`.env.example`** — documented all environment variables with required/optional
  separation for quick Docker Compose setup.
- **`docker-compose.dev.yml`** — standalone development compose with SQLite,
  hot-reload, no-auth, and local gateway mode. No PostgreSQL required.
- **Justfile** — task runner with `dev`, `test`, `lint`, `format`, `check`,
  `docker-build`, `docker-up`, `docker-down`, `docs`, and `sync` targets.
- **Webhooks** — event subscriptions with HMAC-SHA256 signing, Alembic
  migration 002, `WebhookService` with async delivery, and admin API
  (`POST/GET/DELETE /api/webhooks`).

### Changed

- **README overhaul** — new "Why ShoreGuard?" section, dual quick-start paths
  (pip + Docker Compose), collapsible screenshot gallery, expanded development
  section with Justfile references, updated roadmap.
- **Deployment docs expanded** — step-by-step Docker setup, full environment
  variable reference table, backup/restore procedures, network isolation
  explanation, upgrade process, and troubleshooting section.
- **Contributing docs expanded** — "Clone to first sandbox" walkthrough,
  Justfile task runner section, corrected clone URL and port references.
- **Local mode docs expanded** — developer workflow section with `--no-auth`
  combination, SQLite defaults, and state reset instructions.
- **mkdocs nav** — added migration runbook to admin guide navigation.
- Version bumped to `0.13.0`.

### Fixed

- **Duplicate auth log** — removed redundant "Authentication DISABLED" warning
  from `init_auth()` that appeared unformatted when running with `--reload`.
- **Logger name formatting** — replaced one-shot name rewriting with a custom
  `Formatter` that strips the `shoreguard.` prefix at render time, so
  late-created loggers (e.g. `shoreguard.db`) are also shortened correctly.
- **Contributing docs** — corrected clone URL (`your-org` → `FloHofstetter`)
  and port reference (`8000` → `8888`).

## [0.12.0] — 2026-04-03

### Added

- **Inference timeout** — `timeout_secs` field on `PUT /api/gateways/{gw}/inference`
  allows configuring per-route request timeouts (0 = default 60s). Displayed in the
  gateway detail inference card.
- **L7 query parameter matchers** — network policy rules can now match on URL query
  parameters using `glob` (single pattern) or `any` (list of patterns) matchers.

### Changed

- **Protobuf stubs regenerated** from OpenShell v0.0.22 (was ~v0.0.16).

## [0.11.0] — 2026-04-03

### Added

- **Docker containerisation** — multi-stage `Dockerfile` and
  `docker-compose.yml` (ShoreGuard + PostgreSQL) for production deployments.
- **Health probes** — unauthenticated `GET /healthz` (liveness) and
  `GET /readyz` (readiness — checks database and gateway service).
- **`protobuf` runtime dependency** — added to `pyproject.toml` (was
  previously only available transitively via `grpcio-tools` in dev).
- `.dockerignore` for minimal build context.

### Fixed

- **PostgreSQL migration** — `users.is_active` column used
  `server_default=sa.text("1")` which fails on PostgreSQL. Changed to
  `sa.true()` for cross-database compatibility.
- **Gateway health endpoint** — `GET /api/gateways/{gw}/health` called
  `get_client()` directly instead of via dependency injection, causing
  `GatewayNotConnectedError` to return 200 instead of 503.

### Changed

- FastAPI `version` field now matches the package version (was stale at
  `0.8.0`).

## [0.10.0] — 2026-04-03

### Removed

- **"Active gateway" concept** — the server-side `active_gateway` file
  (`~/.config/openshell/active_gateway`) is no longer read or written by the
  web service. Every gateway operation now requires an explicit gateway name
  from the URL. Removed endpoints: `POST /{name}/select`, `GET /info`,
  `POST /start`, `POST /stop`, `POST /restart` (non-named variants). The
  named variants (`/{name}/start` etc.) remain unchanged.
- **`active` field** removed from all gateway API responses (`list`, `info`,
  `register`).
- Service methods removed: `get_active_name()`, `write_active_gateway()`,
  `select()`, `health()`.
- Auto-select of first registered gateway removed from `register()`.

### Changed

- **Stateless gateway routing** — the `name` parameter is now required on
  `get_client()`, `set_client()`, `reset_backoff()`, `get_info()`, and
  `get_config()`. No method falls back to the active gateway file anymore.
- **`GET /info` → `GET /{name}/info`** — gateway info endpoint is now
  name-scoped.
- **`GET /config` → `GET /{name}/config`** — gateway config endpoint is now
  name-scoped.
- **`LocalGatewayManager`** — `start()`, `stop()`, `restart()` now require
  a gateway name. Connection and client management simplified: always
  operates on the explicitly named gateway.
- **Frontend inference config** — now shows when gateway is connected
  (`gw.connected`) instead of when it was the "active" gateway
  (`gw.active`). Gateway list highlights connected gateways.
- **Health store** — uses `GW` directly for gateway name instead of
  fetching from `/api/gateway/info`.
- Version bumped to `0.10.0`.
- 756 tests (down from 774 — 18 tests for removed active-gateway
  functionality deleted).

---

## [0.9.0] — 2026-04-03

### Added

- **Sidebar navigation** — collapsible sidebar with grouped navigation
  (Gateways, Policies, gateway-scoped Sandboxes/Providers, admin-only
  Audit/Users). Replaces the icon buttons in the topbar. Responsive:
  collapses to hamburger menu on mobile (<768px).
- **Light/dark theme toggle** — switchable via sidebar button, persisted
  in `localStorage`. All custom CSS variables scoped to
  `[data-bs-theme]`; Bootstrap 5.3 handles the rest automatically.

### Fixed

- **Audit page breadcrumbs** — audit.html now has breadcrumbs and uses
  the standard layout instead of `container-fluid`.
- **Dashboard breadcrumbs** — dashboard.html now has breadcrumbs.
- **Theme-aware tables** — removed hardcoded `table-dark` class from all
  templates and JS files; tables now adapt to the active theme.

## [0.8.0] — 2026-04-03

### Fixed

- **RBAC response_model crash** — added `response_model=None` to 17 route
  decorators (16 in `pages.py`, 1 in `main.py`) returning `TemplateResponse`,
  `HTMLResponse`, or `RedirectResponse`. Prevents FastAPI Pydantic serialization
  errors on non-JSON responses.
- **IntegrityError/ValueError split** — gateway-role SET endpoints now return
  409 on constraint conflicts and 404 on missing user/SP/gateway, instead of a
  blanket 404 for both.

### Added

- **Migration verification tests** — 5 tests (`tests/test_migrations.py`)
  covering SQLite and PostgreSQL: fresh-DB, head revision, schema-matches-models,
  downgrade, and PostgreSQL fresh-DB.
- **RBAC regression & validation tests** — 10 new tests (`tests/test_rbac.py`)
  for DELETE gateway-role 404s, invalid gateway name 400s, and invalid role 400s
  (user and SP symmetry).
- **Migration check script** — `scripts/verify_migrations.sh` runs all Alembic
  migrations against a fresh database and verifies the final revision.
- **Migration CI workflow** — `.github/workflows/test-migrations.yml` runs
  migration tests on SQLite and PostgreSQL for PRs touching migrations or models.
- **PR template** — `.github/PULL_REQUEST_TEMPLATE.md` with migration checklist.
- **Migration runbook** — `docs/admin/migration-runbook.md` with backup,
  upgrade, and rollback procedures.
- **Warning logs on error paths** — all gateway-role endpoints now log
  `logger.warning()` for invalid names, invalid roles, not-found, and conflict
  responses.
- **Backoff for background tasks** — `_cleanup_operations()` and
  `_health_monitor()` double their interval (up to a cap) after 10 consecutive
  failures and reset on success.
- `postgres` pytest marker in `pyproject.toml`.

### Security

- **Shell injection fix** — `verify_migrations.sh` passes database URL via
  `os.environ` instead of bash interpolation in a Python heredoc.

### Changed

- **Migrations squashed** — all 7 incremental migrations replaced by a single
  `001_initial_schema.py` that creates the final schema directly. Existing
  databases must be reset (`rm ~/.config/shoreguard/shoreguard.db`).
- Migration CI caches `uv` dependencies via `enable-cache: true`.

---

## [0.7.1] — 2026-04-01

### Added

- **API reference docs** — mkdocstrings[python] generates reference pages from
  existing Google-style docstrings. New pages under `docs/reference/`: Client,
  Services, API Internals, Models, and Config & Exceptions.

## [0.7.0] — 2026-04-01

### Added

- **pydoclint integration** — new `[tool.pydoclint]` section in `pyproject.toml`
  with maximum strictness (Google-style, `skip-checking-short-docstrings = false`,
  all checks enabled). Added `pydoclint >= 0.8` as dev dependency.
- **Comprehensive Google-style docstrings** — all 1 193 pydoclint violations
  resolved across the entire codebase. Every function, method, and class now
  has `Args:`, `Returns:`, `Raises:`, and `Yields:` sections as appropriate.
  Compatible with mkdocstrings for future API reference generation.
- **Page templates** — dedicated HTML templates for approval edit, approval
  history, gateway register, gateway roles, policy revisions, and provider
  form pages, replacing Bootstrap modal dialogs.

### Changed

- **Database schema cleanup (migration 007):**
  - Timestamp columns (`registered_at`, `last_seen`, `created_at`, `last_used`,
    `timestamp`) converted from `String` to `DateTime(timezone=True)` across
    `gateways`, `users`, `service_principals`, and `audit_log` tables.
  - `gateways` table rebuilt with auto-incrementing integer primary key (`id`)
    replacing the old `name`-based primary key.
  - `user_gateway_roles` and `sp_gateway_roles` migrated from `gateway_name`
    (String FK) to `gateway_id` (Integer FK) with `ON DELETE CASCADE`.
  - `audit_log` column `gateway` renamed to `gateway_name`; new `gateway_id`
    FK added with `ON DELETE SET NULL`.
- **Audit service refactored** — uses `with session_factory()` context manager
  instead of manual `session.close()` in finally blocks. Gateway ID resolution
  via FK lookup on write.
- Version bumped to `0.7.0`.

### Fixed

- **`GatewayNotConnectedError` in `_try_connect_from_config`** — exception is
  now caught instead of propagating as an unhandled error.
- **`request.state.role` not set from `_require_page_auth`** — page auth
  guard now correctly stores the resolved role in request state.

---

## [0.6.0] — 2026-03-31

### Added

- **Gateway-scoped RBAC** — per-gateway role overrides for users and service
  principals. Alembic migration 006 adds `user_gateway_roles` and
  `sp_gateway_roles` tables.
- **Policy diff viewer** — compare two policy revisions side-by-side.
- **Hardened RBAC** — async correctness improvements and additional test coverage.

---

## [0.5.0] — 2026-03-30

### Added

- **Persistent audit log** — all state-changing operations (sandbox/policy/gateway
  CRUD, user management, approvals, provider changes) are recorded in a database
  table with actor, role, action, resource, gateway context, and client IP.
- **Audit API** — `GET /api/audit` lists entries with filters (actor, action,
  resource type, date range). `GET /api/audit/export?format=csv|json` exports
  the full log. Both endpoints are admin-only.
- **Audit page** — `/audit` admin page with filter inputs, pagination, and
  CSV/JSON export buttons. Built with Alpine.js.
- **Alembic migration 005** — `audit_log` table with indexes on timestamp,
  actor, action, and resource type.
- **Audit cleanup** — entries older than 90 days are automatically purged by
  the existing background cleanup task.

### Fixed

- **Fail-closed auth** — when the database is unavailable, requests are now
  denied with 503 instead of silently granting admin access.
- **Async audit logging** — `audit_log()` is now async and runs DB writes in a
  thread pool via `asyncio.to_thread`, preventing event-loop blocking on every
  state-changing request.
- **UnboundLocalError in AuditService** — `log()`, `list()`, and `cleanup()` no
  longer crash if the session factory itself raises; session is now guarded with
  `None` checks in except/finally blocks.
- **Audit actor for auth events** — login, setup, register, and invite-accept
  now set `request.state.user_id` before calling `audit_log()`, so the audit
  trail records the actual user instead of "unknown".
- **Failed login auditing** — failed login attempts now produce a
  `user.login_failed` audit entry, enabling brute-force detection.
- **Authorization failure auditing** — `require_role()` now writes an
  `auth.forbidden` audit entry when a user is denied access.
- **Audit ordering in approvals** — all six approval endpoints now log the audit
  entry *after* the operation succeeds, preventing false entries on failure.
- **Conditional delete audit** — `sandbox.delete` and `provider.delete` only
  write audit/log entries when the resource was actually deleted.
- **Async background cleanup** — the periodic cleanup task now uses
  `asyncio.to_thread` for DB calls instead of blocking the event loop.
- **Gateway retry button** — the "Retry" button in the gateway error banner now
  correctly calls `Alpine.store('health').check()` instead of the removed
  `checkGatewayHealth()` function.

### Changed

- **Frontend migrated to Alpine.js** — all 20+ pages rewritten from Vanilla JS
  template-literal rendering (`innerHTML = renderX(data)`) to Alpine.js reactive
  directives (`x-data`, `x-for`, `x-text`, `x-show`, `@click`). No build step
  required — Alpine.js loaded via CDN.
- **Three Alpine stores** replace scattered global state:
  - `auth` — role, email, authenticated status (replaces inline script + `window.SG_ROLE`)
  - `toasts` — notification queue (replaces `showToast()` DOM manipulation)
  - `health` — gateway connectivity monitoring (replaces `checkGatewayHealth()` globals)
- **XSS surface reduced** — Alpine's `x-text` auto-escapes all dynamic content,
  eliminating the need for manual `escapeHtml()` calls in templates.
- **Render functions removed** — `renderGatewayTable()`, `renderSandboxList()`,
  `renderDashboard()`, and ~50 other `renderX()` functions replaced by declarative
  Alpine templates in HTML.
- **`app.js` slimmed** — reduced from ~340 lines to ~95 lines. Only retains
  `apiFetch()`, `showConfirm()`, `escapeHtml()`, `formatTimestamp()`, `navigateTo()`,
  and URL helpers.
- **WebSocket integration** — sandbox detail, logs, and approvals pages receive
  live updates via `CustomEvent` dispatching from `websocket.js` to Alpine components.
- Version bumped to `0.5.0`.
- **717 tests** (up from 710), including audit service, API route, and DB schema tests.

---

## [0.4.0] — 2026-03-30

### Added

- **User-based RBAC** — three-tier role hierarchy (admin → operator → viewer)
  replaces the single shared API key. Users authenticate with email + password
  via session cookies; service principals use Bearer tokens for API/CI access.
- **Invite flow** — admins invite users by email. The invite generates a
  single-use, time-limited token (7 days). The invitee sets their password on
  the `/invite` page and receives a session cookie.
- **Self-registration** — opt-in via `SHOREGUARD_ALLOW_REGISTRATION=1`.
  New users register as viewers. Disabled by default.
- **Setup wizard** — first-run `/setup` page creates the initial admin account.
  All API access is blocked until setup is complete.
- **Service principals** — named API keys with roles, created by admins.
  Keys are SHA-256 hashed (never stored in plaintext). `last_used` timestamp
  tracked on each request.
- **User management UI** — `/users` page for admins with invite form, role
  badges, and delete actions. Dedicated `/users/new` and
  `/users/new-service-principal` pages replace the old modal dialogs.
- **Error pages** — styled error pages for 403, 404, and other HTTP errors
  instead of raw JSON responses in the browser.
- **User email in navbar** — logged-in user email and role badge shown in the
  navigation bar.
- **Alembic migrations 002–004** — `api_keys` table, `users` +
  `service_principals` tables with FK constraints, invite token hashing.
- **CLI commands** — `create-user`, `delete-user`, `list-users`,
  `create-service-principal`, `delete-service-principal`, `list-service-principals`.
- **710 tests** (up from 635), including comprehensive RBAC, auth flow,
  invite expiry, self-deletion guard, and last-admin protection tests.

### Changed

- **Auth module rewritten** — `shoreguard/api/auth.py` expanded from ~100
  to ~700 lines. Session tokens are HMAC-signed with a 5-part format
  (`nonce.expiry.user_id.role.signature`). Roles are always verified against
  the database, not the session token, so demotions take effect immediately.
- **All state-changing endpoints** now enforce minimum role via
  `require_role()` FastAPI dependency (admin for user/SP management and
  gateway registration; operator for sandbox/policy/provider operations).
- **Frontend role-based UI** — buttons and nav items hidden based on role
  via `data-sg-min-role` attributes. `escapeHtml()` used consistently
  across all JavaScript files.
- **Policies router split** — preset routes (`/api/policies/presets`) are
  mounted globally; sandbox policy routes remain gateway-scoped only.
  Fixes a bug where `/api/sandboxes/{name}/policy` was reachable without
  gateway context.
- **Audit logging standardised** — all log messages use `actor=` consistently.
  Role denials now include method, path, and actor. IntegrityError on
  duplicate user/SP creation is logged. Logout resolves email instead of
  numeric user ID.

### Fixed

- **Timing attack in `authenticate_user()`** — bcrypt verification now runs
  against a dummy hash when the user does not exist, preventing email
  enumeration via response time analysis.
- **Policies router double-inclusion** — the full policies router was mounted
  both globally and under the gateway prefix, exposing sandbox policy routes
  without gateway context. Now only preset routes are global.
- **Missing exception handling** — `is_setup_complete()`, `list_users()`, and
  `list_service_principals()` now catch `SQLAlchemyError` instead of letting
  database errors propagate as 500s.
- **`verify_password()` bare Exception catch** — narrowed to
  `(ValueError, TypeError)` to avoid masking unexpected errors.
- **WebSocket XSS** — `sandboxName` in toast messages is now escaped with
  `escapeHtml()`. Log level CSS class validated against a whitelist.
- **`delete_filesystem_path` missing Query annotation** — `path` parameter
  now uses explicit `Query(...)` instead of relying on FastAPI inference.
- **Migration 004 downgrade** documented as non-reversible (SHA-256 hashes
  cannot be reversed; pending invites are invalidated on downgrade).

### Security

- Constant-time authentication prevents timing-based email enumeration.
- Invite tokens are SHA-256 hashed in the database (migration 004).
- Session invalidation on user deletion and deactivation — existing sessions
  are rejected on the next request.
- Last-admin guard with database-level `FOR UPDATE` lock prevents TOCTOU race.
- Self-deletion guard prevents admins from deleting their own account.
- Email normalisation (`.strip().lower()`) prevents duplicate accounts.
- Password length enforced (8–128 characters) on all auth endpoints.
- XSS escaping hardened across all frontend JavaScript files.

### Dependencies

- Added `pwdlib[bcrypt]` — password hashing with bcrypt.

---

## [0.3.0] — 2026-03-28

### Added

- **Central gateway management** — Shoreguard transforms from a local sidecar
  into a central management plane for multiple remote OpenShell gateways (like
  Rancher for Kubernetes clusters). Gateways are deployed independently and
  registered with Shoreguard via API.
- **SQLAlchemy ORM + Alembic** — persistent gateway registry backed by
  SQLAlchemy with automatic embedded migrations on startup. SQLite by default,
  PostgreSQL via `SHOREGUARD_DATABASE_URL` for container deployments.
- **Gateway registration API** — `POST /api/gateway/register` to register
  remote gateways with endpoint, auth mode, and mTLS certificates.
  `DELETE /api/gateway/{name}` to unregister. `POST /{name}/test-connection`
  to explicitly test connectivity.
- **`ShoreGuardClient.from_credentials()`** — new factory method that accepts
  raw certificate bytes from the database instead of filesystem paths.
- **Background health monitor** — probes all registered gateways every 30
  seconds and updates health status (`last_seen`, `last_status`) in the
  registry.
- **`import-gateways` CLI command** — imports gateways from openshell filesystem
  config (`~/.config/openshell/gateways/`) into the database, including mTLS
  certificates. Replaces the old `migrate-v2` command.
- **`SHOREGUARD_DATABASE_URL`** — environment variable to configure an external
  database (PostgreSQL) for container/multi-instance deployments.
- **`--local` / `SHOREGUARD_LOCAL_MODE`** — opt-in flag to enable local Docker
  container lifecycle management (start/stop/restart/create/destroy). In local
  mode, filesystem gateways are auto-imported into the database on startup.
- **`--database-url` / `SHOREGUARD_DATABASE_URL`** — all env vars now also
  available as CLI flags.

### Changed

- **GatewayService refactored** — reduced from ~800 to ~250 lines. Gateway
  discovery now queries the SQLAlchemy registry instead of scanning the
  filesystem. Connection management (backoff, health checks) preserved.
- **Docker/CLI methods extracted** to `LocalGatewayManager`
  (`shoreguard/services/local_gateway.py`), only active in local mode.
- **Frontend updated** — "Create Gateway" replaced with "Register Gateway"
  modal (endpoint, auth mode, PEM certificate upload). Start/Stop/Restart
  buttons replaced with "Test Connection". "Destroy" renamed to "Unregister".
  New "Last Seen" column, Port column removed.
- **API route changes** — `POST /create` (202 LRO) → `POST /register` (201
  sync). `POST /{name}/destroy` → `DELETE /{name}`. Local lifecycle routes
  (start/stop/restart/diagnostics) return 404 unless `SHOREGUARD_LOCAL_MODE=1`.
- **Request-level logging** — gateway register, unregister, test-connection,
  and select routes now log at INFO/WARNING level. `LocalGatewayManager` logs
  Docker daemon errors, port conflicts, missing openshell CLI, and openshell
  command failures.
- **`api/main.py` split into modules** — extracted `cli.py` (Typer CLI +
  import logic), `pages.py` (HTML routes + auth endpoints), `websocket.py`
  (WebSocket handler), and `errors.py` (exception handlers). `main.py`
  reduced from 1 084 to ~190 lines (pure wiring).
- Version bumped to `0.3.0`.
- Test suite rewritten for registry-backed architecture (635 tests).
- **Logger names standardised** — all modules now use `getLogger(__name__)`
  instead of hardcoded `"shoreguard"`. Removes duplicate log lines caused
  by parent-logger propagation.
- **Unified log format** — single format (`HH:MM:SS LEVEL module message`)
  shared by shoreguard and uvicorn loggers with fixed-width aligned columns.
- Duplicate "API-key authentication enabled" log line removed.

### Fixed

- **SSRF protection** — `_is_private_ip()` now performs real DNS resolution
  instead of `AI_NUMERICHOST`. Hostnames that resolve to private/loopback/
  link-local addresses are correctly blocked. Includes a 2 s DNS timeout.
- **`import-gateways` crash on single gateway** — `registry.register()` failures
  no longer abort the entire import; individual errors are logged and
  skipped.
- **`from_active_cluster` error handling** — missing metadata files, corrupt
  JSON, and missing `gateway_endpoint` keys now raise
  `GatewayNotConnectedError` with a clear message instead of raw
  `FileNotFoundError` / `KeyError`.
- **`init_db()` failure logging** — database initialisation errors in the
  FastAPI lifespan are now logged before re-raising.
- **`_get_gateway_service()` guard** — raises `RuntimeError` if called before
  the app lifespan has initialised the service (instead of `AttributeError`
  on `None`).
- **WebSocket `RuntimeError` swallowed** — `RuntimeError` during
  `websocket.send_json()` is now debug-logged instead of silently passed.
- **SQLite pragma errors** — failures setting WAL/busy_timeout/synchronous
  pragmas are now logged as warnings.
- **`_import_filesystem_gateways` SSRF gap** — filesystem-imported gateways
  were not checked against `is_private_ip()`. Now blocked in non-local mode,
  consistent with the API registration endpoint.
- **`_import_filesystem_gateways` skipped count** — corrupt metadata JSON was
  logged but not counted in the `skipped` total, making the summary misleading.
- **`_import_filesystem_gateways` mTLS read error** — `read_bytes()` on cert
  files had no error handling (TOCTOU race). Now wrapped in try/except with
  a 64 KB size limit matching the API route.
- **`check_all_health` DB error isolation** — a database error updating health
  for one gateway no longer prevents health updates for all remaining gateways.
- **`select()` implicit name resolution** — `get_client()` was called without
  `name=`, relying on a filesystem round-trip via `active_gateway` file. Now
  passes the name explicitly.
- **CLI `import-gateways` NameError** — if `init_db()` failed, `engine` was
  undefined and `engine.dispose()` in the `finally` block raised `NameError`.
- **DB engine not disposed on shutdown** — the SQLAlchemy engine was not
  disposed during FastAPI lifespan shutdown, skipping the SQLite WAL
  checkpoint.
- **Docker start/stop errors silently swallowed** — `SubprocessError`/`OSError`
  in `_docker_start_container`/`_docker_stop_container` was caught but never
  logged.
- **Gateway start retry without summary** — when all 10 connection retries
  failed after a gateway start, no warning was logged.
- **Frontend 404 on gateway list page** — `inference-providers` was fetched
  without a gateway context, hitting a non-existent global route.

### Security

- SSRF DNS resolution bypass fixed (hostnames resolving to RFC 1918 / loopback
  addresses were not blocked).
- SSRF validation includes DNS timeout protection (2 s) to prevent slow-DNS
  attacks.
- **`remote_host` input validation** — `CreateGatewayRequest.remote_host` is
  now validated with a hostname regex (max 253 chars) before being passed to
  subprocess.
- **SSRF check skipped in local mode** — `is_private_ip()` checks at
  connect-time and import-time now allow private/loopback addresses when
  `SHOREGUARD_LOCAL_MODE` is set, since locally managed gateways always run
  on `127.0.0.1`.

### Dependencies

- Added `sqlalchemy >= 2.0` (runtime) — ORM and database abstraction.
- Added `alembic >= 1.15` (runtime) — embedded schema migrations on startup.

## [0.2.0] — 2026-03-27

### Added

- **API-key authentication** — optional shared API key via `--api-key` flag or
  `SHOREGUARD_API_KEY` env var. Supports Bearer tokens, HMAC-signed session
  cookies, and WebSocket query-param auth. Zero-config local development
  remains unchanged (auth is a no-op when no key is set).
- **Login page** for the web UI with session cookie management and automatic
  redirect for unauthenticated users.
- **Long-Running Operations (LRO)** — gateway and sandbox creation now return
  `202 Accepted` with an operation ID. Clients can poll `/api/operations/{id}`
  for progress. Includes automatic cleanup of expired operations.
- **`force` flag for gateway destroy** with dependency checking — prevents
  accidental deletion of gateways that still have running sandboxes unless
  `--force` is passed.
- **UNIMPLEMENTED error handling** — gRPC `UNIMPLEMENTED` errors now return a
  human-readable 501 response with feature context instead of a generic 500.
- OpenAPI documentation is automatically hidden when authentication is enabled.
- Session cookies set `secure` flag automatically when served over HTTPS.
- **`DEADLINE_EXCEEDED` mapping** — gRPC `DEADLINE_EXCEEDED` wird jetzt auf
  HTTP 504 (Gateway Timeout) gemappt.
- **`ValidationError` exception** — neuer Fehlertyp für Eingabevalidierung
  (ungültige Namen, shlex-Fehler) mit HTTP 400 Response.
- **Gateway/Sandbox name validation** — Regex-basierte Validierung von
  Ressourcennamen zur Verhinderung von Argument-Injection.
- **Client-IP tracking** — Client-IP wird bei Auth-Fehlern und
  Login-Fehlversuchen mitgeloggt.

### Changed

- Sandbox creation returns `202 Accepted` (was `201 Created`) to reflect
  the asynchronous LRO pattern.
- Destroyed gateways are now filtered from the gateway list by default.
- Version bumped to `0.2.0`.
- Exception-Handler im gesamten Codebase von breitem `except Exception` auf
  spezifische Typen (`grpc.RpcError`, `OSError`, `ssl.SSLError`,
  `ConnectionError`, `TimeoutError`) eingeschränkt.
- Logging deutlich erweitert: Debug-Logging für bisher stille Pass-Blöcke,
  Error-Level für Status ≥ 500, Warning-Level für Status < 500.
- WebSocket-Auth-Logging von INFO/WARNING auf DEBUG normalisiert.
- `friendly_grpc_error()` prüft jetzt freundliche Nachrichten vor Raw-Details.

### Fixed

- Auth credential check logic deduplicated into a single `check_request_auth()`
  helper shared by API dependencies, the `/api/auth/check` endpoint, and page
  auth guards.
- **Fire-and-forget Task-GC** — Background-Tasks werden jetzt in einem Set
  gehalten, um Garbage-Collection durch asyncio zu verhindern.
- **Cross-Thread WebSocket-Signaling** — `asyncio.Event` durch
  `threading.Event` ersetzt für korrekte Thread-übergreifende Signalisierung.
- **WebSocket Queue-Overflow** — `QueueFull`-Exception wird abgefangen mit
  Fallback auf `cancel_event`.
- **Event-Loop-Blocking** — `get_client()` im WebSocket-Handler mit
  `asyncio.to_thread()` gewrappt.
- **gRPC-Client-Leak** — Client-Leak in `_try_connect()` behoben, wenn
  Health-Check fehlschlägt.
- **Login-Redirect-Validation** — Open-Redirect-Schutz: URLs die nicht mit `/`
  beginnen oder mit `//` starten werden abgelehnt.
- **Error-Message-Sanitization** — `friendly_grpc_error()` verhindert, dass
  rohe gRPC-Fehlermeldungen an API-Clients geleitet werden.
- **Thread-Safety** — `threading.Lock` für `GatewayService._clients` und
  thread-safe Reads in `OperationStore.to_dict()`.
- **YAML-Parsing-Robustheit** — `YAMLError`, None- und Skalar-Werte werden in
  `presets.py` abgefangen.
- **Metadata-Datei-Robustheit** — `JSONDecodeError` und `OSError` bei
  Gateway-Metadata-Reads mit Fallback behandelt.

### Security

- Open-Redirect-Schutz auf der Login-Seite.
- API-Fehlermeldungen werden sanitisiert, um interne Details nicht preiszugeben.
- Thread-sichere Client-Verwaltung und Operation-Store-Zugriffe.
- Argument-Injection-Prävention durch Regex-Namensvalidierung.
- Client-IP-Logging bei Auth-Events für Security-Monitoring.

## [0.1.0] — 2026-03-25

Initial release.

### Added

- **Sandbox management** — create, list, get, delete sandboxes with custom
  images, environment variables, GPU support, and provider integrations.
- **Real-time monitoring** — WebSocket streaming of sandbox logs, events, and
  status changes.
- **Command execution** — run commands inside sandboxes with stdout/stderr
  capture.
- **SSH sessions** — create and revoke interactive SSH terminal sessions.
- **Security policy editor** — visual network rule, filesystem access, and
  process/Landlock policy management without raw YAML editing.
- **Policy approval workflow** — review, approve, reject, or edit agent-
  requested endpoint rules with real-time WebSocket notifications.
- **Policy presets** — 9 bundled templates (PyPI, npm, Docker Hub, NVIDIA NGC,
  HuggingFace, Slack, Discord, Telegram, Jira, Microsoft Outlook).
- **Multi-gateway support** — manage multiple OpenShell gateways with status
  monitoring, diagnostics, and automatic reconnection.
- **Provider management** — CRUD for inference/API providers with credential
  templates and community sandbox browser.
- **Sandbox wizard** — guided step-by-step sandbox creation with agent type
  selection and one-click preset application.
- **Web dashboard** — responsive Bootstrap 5 UI with gateway, sandbox, policy,
  approval, log, and terminal views.
- **REST API** — full async FastAPI backend with Swagger UI documentation.
- **CLI** — `shoreguard` command with configurable host, port, log level, and
  auto-reload.
