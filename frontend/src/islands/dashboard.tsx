/** Dashboard page (island): stat cards, recent activity, gateway status. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { auth, ensureAuth, hasRole } from "../lib/auth";
import { badgeClass } from "../lib/constants";
import { formatTimeAgo } from "../lib/format";
import { ErrorAlert, Spinner } from "../lib/widgets";
import { LocalGatewayCreate } from "./local-gateway-create";

interface GatewayItem {
  name: string;
  status?: string;
}

interface AuditEntry {
  id: number;
  action?: string;
  actor?: string;
  resource_type?: string;
  resource_id?: string;
  timestamp?: string;
}

interface Digest {
  window_hours: number;
  message: string;
  audit: { total: number; by_action: Record<string, number>; forbidden: number };
  sandboxes: { created: number; deleted: number };
  approvals: { approved: number; rejected: number; votes: number };
  gateways: { total: number; unreachable: string[] };
  webhook_failures: number;
  kill_switch_engaged: string[];
  spending?: {
    today_total: number;
    today_total_cost?: number;
    currency_label?: string;
    top: { gateway: string; sandbox: string; requests: number; estimated_cost?: number }[];
  };
}

interface SbRaw {
  name: string;
  phase?: string;
}

interface UsageRow {
  gateway: string;
  sandbox: string;
  requests: number;
  estimated_cost?: number;
}

export interface FlatSandbox {
  gateway: string;
  name: string;
  phase: string;
}

export interface SbActivity {
  gateway: string;
  name: string;
  phase: string;
  requests: number;
  cost: number;
  pending: number;
}

/**
 * Join every gateway's sandboxes with cross-gateway usage and pending-approval
 * counts into one at-a-glance activity row per sandbox. The home dashboard has
 * no single "active" gateway, so this aggregates the whole fleet — for a solo
 * box that is exactly the single-pane "what are my agents doing?" view.
 *
 * @param flat - Sandboxes across all gateways (gateway + name + phase).
 * @param usageTop - Rows from `/api/usage/summary` (all gateways).
 * @param pendings - Pending-approval counts, index-aligned to `flat`.
 * @returns One activity row per sandbox.
 */
export function buildActivity(
  flat: FlatSandbox[],
  usageTop: UsageRow[],
  pendings: number[],
): SbActivity[] {
  const reqByKey = new Map<string, number>();
  const costByKey = new Map<string, number>();
  usageTop.forEach((t) => {
    reqByKey.set(`${t.gateway}/${t.sandbox}`, t.requests);
    costByKey.set(`${t.gateway}/${t.sandbox}`, t.estimated_cost ?? 0);
  });
  return flat
    .map((s, i) => ({
      gateway: s.gateway,
      name: s.name,
      phase: s.phase,
      requests: reqByKey.get(`${s.gateway}/${s.name}`) ?? 0,
      cost: costByKey.get(`${s.gateway}/${s.name}`) ?? 0,
      pending: pendings[i] ?? 0,
    }))
    // Busiest sandboxes first — mission control should lead with what is active.
    .sort(
      (a, b) =>
        b.requests - a.requests || b.pending - a.pending || a.name.localeCompare(b.name),
    );
}

function SandboxActivityCard({ rows }: { rows: SbActivity[] }) {
  const multiGw = new Set(rows.map((r) => r.gateway)).size > 1;
  // Only show the estimated-cost column when pricing is on (some row has a cost).
  const hasCost = rows.some((r) => r.cost > 0);
  return (
    <div class="card sg-card-themed mb-4">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <h6 class="mb-0">
            <i class="bi bi-activity me-2" />
            Sandbox activity{" "}
            <span class="text-muted small fw-normal">last 24h, across all gateways</span>
          </h6>
        </div>
        {rows.length === 0 ? (
          <div class="text-center text-muted py-3">
            <i class="bi bi-grid fs-3 d-block mb-2" />
            <p class="small mb-0">No sandboxes running yet.</p>
          </div>
        ) : (
          <div class="table-responsive">
            <table class="table table-sm align-middle mb-0">
              <thead>
                <tr>
                  <th>Sandbox</th>
                  {multiGw && <th>Gateway</th>}
                  <th>Phase</th>
                  <th class="text-end">24h requests</th>
                  {hasCost && <th class="text-end">24h est. $</th>}
                  <th class="text-end">Approvals</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={`${s.gateway}/${s.name}`}>
                    <td>
                      <a
                        href={`/gateways/${s.gateway}/sandboxes/${s.name}`}
                        class="text-decoration-none fw-medium"
                      >
                        {s.name}
                      </a>
                    </td>
                    {multiGw && <td class="small text-muted">{s.gateway}</td>}
                    <td>
                      <span class={`badge ${badgeClass("sandbox", s.phase)}`}>{s.phase}</span>
                    </td>
                    <td class="text-end font-monospace">{s.requests.toLocaleString()}</td>
                    {hasCost && (
                      <td class="text-end font-monospace">
                        {s.cost > 0 ? `$${s.cost.toFixed(2)}` : "—"}
                      </td>
                    )}
                    <td class="text-end">
                      {s.pending > 0 ? (
                        <a
                          href={`/gateways/${s.gateway}/sandboxes/${s.name}/approvals`}
                          class="badge text-bg-warning text-decoration-none"
                        >
                          {s.pending} pending
                        </a>
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function DigestCard() {
  const [digest, setDigest] = useState<Digest | null>(null);

  useEffect(() => {
    apiFetch<Digest>(`/api/digest`)
      .then(setDigest)
      .catch(() => setDigest(null));
  }, []);

  if (!digest) return null;
  const hasWarnings =
    digest.gateways.unreachable.length > 0 ||
    digest.kill_switch_engaged.length > 0 ||
    digest.webhook_failures > 0 ||
    digest.audit.forbidden > 0;

  return (
    <div class={`card sg-card-themed mb-4 ${hasWarnings ? "border-warning" : ""}`}>
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <h6 class="mb-0">
            <i class="bi bi-sunrise me-2" />
            Last {digest.window_hours}h
          </h6>
          {hasWarnings && (
            <span class="badge text-bg-warning">
              <i class="bi bi-exclamation-triangle me-1" />
              needs attention
            </span>
          )}
        </div>
        <div class="mb-2">{digest.message}</div>
        <div class="d-flex flex-wrap gap-3 small text-muted">
          <span>
            <i class="bi bi-journal-text me-1" />
            {digest.audit.total} audit events
          </span>
          <span>
            <i class="bi bi-grid me-1" />+{digest.sandboxes.created}/−{digest.sandboxes.deleted}{" "}
            sandboxes
          </span>
          <span>
            <i class="bi bi-check-circle me-1" />
            {digest.approvals.approved} approved, {digest.approvals.rejected} rejected
          </span>
          {digest.spending && digest.spending.today_total > 0 && (
            <span>
              <i class="bi bi-lightning-charge me-1" />
              {digest.spending.today_total.toLocaleString()} requests today
              {digest.spending.today_total_cost
                ? ` (est. $${digest.spending.today_total_cost.toFixed(2)})`
                : ""}
              {digest.spending.top[0] ? ` · top: ${digest.spending.top[0].sandbox}` : ""}
            </span>
          )}
          {digest.webhook_failures > 0 && (
            <span class="text-warning">
              <i class="bi bi-broadcast me-1" />
              {digest.webhook_failures} webhook failures
            </span>
          )}
          {digest.gateways.unreachable.map((name) => (
            <a key={name} href={`/gateways/${name}`} class="text-danger text-decoration-none">
              <i class="bi bi-exclamation-octagon me-1" />
              {name} unreachable
            </a>
          ))}
          {digest.kill_switch_engaged.map((name) => (
            <a key={name} href={`/gateways/${name}`} class="text-danger text-decoration-none">
              <i class="bi bi-sign-stop me-1" />
              kill switch on {name}
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

interface NodeStats {
  scope: string;
  cpu: { count: number; load_1m: number; load_5m: number; load_15m: number } | null;
  memory: { total_mb: number; available_mb: number; used_pct: number } | null;
  disk: { total_gb: number; free_gb: number; used_pct: number } | null;
  gpus: {
    name: string;
    utilization_pct: number | null;
    memory_used_mb: number | null;
    memory_total_mb: number | null;
    temperature_c: number | null;
    power_w: number | null;
  }[];
}

interface NodeAlerts {
  enabled: boolean;
  thresholds: Record<string, number>;
  breached: string[];
}

interface UpdateStatus {
  current: string;
  latest: string | null;
  update_available: boolean;
  gateway_versions: Record<string, string>;
  version_skew: boolean;
}

function UpdateBanner() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);

  useEffect(() => {
    apiFetch<UpdateStatus>(`/api/system/updates`)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  if (!status || (!status.update_available && !status.version_skew)) return null;
  return (
    <div class="alert alert-info d-flex flex-wrap align-items-center gap-2 mb-3">
      <i class="bi bi-arrow-up-circle" />
      {status.update_available && (
        <span>
          ShoreGuard <strong>{status.latest}</strong> is available (running {status.current}).
        </span>
      )}
      {status.version_skew && (
        <span>
          Gateways run <strong>different OpenShell versions</strong>:{" "}
          {Object.entries(status.gateway_versions)
            .map(([gw, v]) => `${gw}=${v}`)
            .join(", ")}
          .
        </span>
      )}
    </div>
  );
}

function UsageBar({ label, pct, detail }: { label: string; pct: number; detail: string }) {
  const cls = pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-success";
  return (
    <div class="mb-2">
      <div class="d-flex justify-content-between small">
        <span>{label}</span>
        <span class="text-muted">{detail}</span>
      </div>
      <div class="progress" style={{ height: "6px" }}>
        <div class={`progress-bar ${cls}`} style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
    </div>
  );
}

function NodeStatsCard() {
  const [stats, setStats] = useState<NodeStats | null>(null);
  const [alerts, setAlerts] = useState<NodeAlerts | null>(null);

  useEffect(() => {
    let timer: number | undefined;
    const load = () => {
      apiFetch<NodeStats>(`/api/system/node-stats`)
        .then(setStats)
        .catch(() => setStats(null));
      apiFetch<NodeAlerts>(`/api/system/node-alerts`)
        .then(setAlerts)
        .catch(() => setAlerts(null));
    };
    load();
    timer = window.setInterval(load, 15000);
    return () => window.clearInterval(timer);
  }, []);

  if (!stats || (!stats.cpu && !stats.memory)) return null;
  const cpuPct = stats.cpu ? Math.round((stats.cpu.load_5m / Math.max(1, stats.cpu.count)) * 100) : 0;

  return (
    <div class="card sg-card-themed h-100">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <h6 class="mb-0">
            <i class="bi bi-cpu me-2" />
            This machine
          </h6>
          <span>
            {alerts && alerts.breached.length > 0 && (
              <span
                class="badge text-bg-danger me-1"
                title={`Thresholds breached: ${alerts.breached.join(", ")}`}
              >
                <i class="bi bi-thermometer-high me-1" />
                {alerts.breached.length} alert{alerts.breached.length > 1 ? "s" : ""}
              </span>
            )}
            <span class="badge text-bg-secondary" title="Stats for the host running ShoreGuard">
              host
            </span>
          </span>
        </div>
        {stats.cpu && (
          <UsageBar
            label="CPU"
            pct={cpuPct}
            detail={`load ${stats.cpu.load_5m} / ${stats.cpu.count} cores`}
          />
        )}
        {stats.memory && (
          <UsageBar
            label="Memory"
            pct={stats.memory.used_pct}
            detail={`${Math.round((stats.memory.total_mb - stats.memory.available_mb) / 1024)} / ${Math.round(stats.memory.total_mb / 1024)} GB`}
          />
        )}
        {stats.gpus.map((gpu, i) => (
          <div key={i}>
            <UsageBar
              label={`GPU ${stats.gpus.length > 1 ? i + " " : ""}util`}
              pct={gpu.utilization_pct ?? 0}
              detail={`${gpu.name}${gpu.temperature_c != null ? ` · ${gpu.temperature_c}°C` : ""}${gpu.power_w != null ? ` · ${Math.round(gpu.power_w)}W` : ""}`}
            />
            {gpu.memory_total_mb != null && gpu.memory_used_mb != null && gpu.memory_total_mb > 0 && (
              <UsageBar
                label="GPU memory"
                pct={Math.round((gpu.memory_used_mb / gpu.memory_total_mb) * 100)}
                detail={`${Math.round(gpu.memory_used_mb / 1024)} / ${Math.round(gpu.memory_total_mb / 1024)} GB`}
              />
            )}
          </div>
        ))}
        {stats.disk && (
          <UsageBar
            label="Disk"
            pct={stats.disk.used_pct}
            detail={`${stats.disk.free_gb} GB free`}
          />
        )}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [gateways, setGateways] = useState<GatewayItem[]>([]);
  const [sandboxCount, setSandboxCount] = useState(0);
  const [presetCount, setPresetCount] = useState(0);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [sandboxActivity, setSandboxActivity] = useState<SbActivity[]>([]);
  const [approvalCount, setApprovalCount] = useState(0);

  useEffect(() => {
    (async () => {
      await ensureAuth();
      try {
        const [gwData, presets] = await Promise.all([
          apiFetch<{ items?: GatewayItem[] }>(`/api/gateway/list`).catch(() => null),
          apiFetch<unknown[]>(`/api/policies/presets`).catch(() => null),
        ]);
        const gws = gwData?.items ?? [];
        setGateways(gws);
        setPresetCount((presets ?? []).length);

        // Cross-gateway sandbox activity: the home dashboard has no single active
        // gateway, so aggregate the whole fleet into one mission-control view.
        try {
          const usage = await apiFetch<{ top?: UsageRow[] }>(`/api/usage/summary?days=1`).catch(
            () => null,
          );
          const perGw = await Promise.all(
            gws.map(async (g): Promise<FlatSandbox[]> => {
              try {
                const sbs = await apiFetch<SbRaw[] | { items?: SbRaw[] }>(
                  `/api/gateways/${g.name}/sandboxes`,
                );
                const items = Array.isArray(sbs) ? sbs : (sbs?.items ?? []);
                return items.map((s) => ({
                  gateway: g.name,
                  name: s.name,
                  phase: s.phase ?? "unknown",
                }));
              } catch {
                return [];
              }
            }),
          );
          const flat = perGw.flat().slice(0, 40); // cap the per-sandbox approval fan-out
          setSandboxCount(flat.length);
          const pendings = await Promise.all(
            flat.map((s) =>
              apiFetch<unknown[]>(
                `/api/gateways/${s.gateway}/sandboxes/${s.name}/approvals/pending`,
              )
                .then((a) => (Array.isArray(a) ? a.length : 0))
                .catch(() => 0),
            ),
          );
          const activity = buildActivity(flat, usage?.top ?? [], pendings);
          setSandboxActivity(activity);
          setApprovalCount(activity.reduce((n, s) => n + s.pending, 0));
        } catch {
          // gateways unreachable — leave the activity view empty
        }

        try {
          const audit = await apiFetch<{ entries?: AuditEntry[] }>(`/api/audit?limit=10`);
          setAuditEntries(audit?.entries ?? []);
        } catch {
          // non-admin or audit unavailable
        }
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <Spinner message="Loading dashboard..." />;
  if (error) return <ErrorAlert message={error} />;

  const connectedCount = gateways.filter((g) => g.status === "connected").length;
  const isAdmin = auth.value.loaded && hasRole("admin");
  const firstGw = gateways[0]?.name ?? "";
  // With a single gateway (the solo-box case) jump straight to its sandboxes;
  // with several, the gateway picker is the right next step.
  const sandboxesHref = gateways.length === 1 ? `/gateways/${firstGw}/sandboxes` : "/gateways";

  return (
    <div class="sg-fade-in">
      <UpdateBanner />
      <div class="row g-3 mb-4">
        <div class="col-6 col-lg-3">
          <a href="/gateways" class="text-decoration-none">
            <div class="card sg-card-themed h-100">
              <div class="card-body">
                <div class="d-flex align-items-center mb-2">
                  <i class="bi bi-hdd-network text-info me-2" />
                  <span class="small text-muted">Gateways</span>
                </div>
                <div class="fs-2 fw-bold">{gateways.length}</div>
                <span class="text-muted small">{connectedCount} connected</span>
              </div>
            </div>
          </a>
        </div>

        <div class="col-6 col-lg-3">
          <a href={sandboxesHref} class="text-decoration-none">
            <div class="card sg-card-themed h-100">
              <div class="card-body">
                <div class="d-flex align-items-center mb-2">
                  <i class="bi bi-grid text-success me-2" />
                  <span class="small text-muted">Sandboxes</span>
                </div>
                <div class="fs-2 fw-bold">{sandboxCount}</div>
                <span class="text-muted small">running</span>
              </div>
            </div>
          </a>
        </div>

        <div class="col-6 col-lg-3">
          <a href="/approvals" class="text-decoration-none">
            <div class="card sg-card-themed h-100">
              <div class="card-body">
                <div class="d-flex align-items-center mb-2">
                  <i class="bi bi-check-circle text-warning me-2" />
                  <span class="small text-muted">Approvals</span>
                </div>
                <div class="fs-2 fw-bold">{approvalCount}</div>
                <span class="text-muted small">pending</span>
              </div>
            </div>
          </a>
        </div>

        <div class="col-6 col-lg-3">
          <a href="/policies" class="text-decoration-none">
            <div class="card sg-card-themed h-100">
              <div class="card-body">
                <div class="d-flex align-items-center mb-2">
                  <i class="bi bi-shield-plus text-warning me-2" />
                  <span class="small text-muted">Policy Presets</span>
                </div>
                <div class="fs-2 fw-bold">{presetCount}</div>
                <span class="text-muted small">available</span>
              </div>
            </div>
          </a>
        </div>
      </div>

      <DigestCard />

      {gateways.length > 0 && <SandboxActivityCard rows={sandboxActivity} />}

      <div class="row g-3 mb-4">
        {isAdmin && (
          <div class="col-lg-5">
            <div class="card sg-card-themed h-100">
              <div class="card-body">
                <div class="d-flex justify-content-between align-items-center mb-3">
                  <h6 class="mb-0">
                    <i class="bi bi-journal-text me-2" />
                    Recent Activity
                  </h6>
                  <a href="/audit" class="small text-decoration-none text-muted">
                    View all <i class="bi bi-arrow-right" />
                  </a>
                </div>
                {auditEntries.length > 0 ? (
                  <div class="list-group list-group-flush">
                    {auditEntries.map((entry) => (
                      <div key={entry.id} class="list-group-item bg-transparent border-secondary px-0 py-2">
                        <div class="d-flex justify-content-between align-items-start">
                          <div>
                            <span class="badge text-bg-secondary me-1 sg-fs-sm2">
                              {entry.action}
                            </span>
                            <span class="small">
                              {entry.resource_type}
                              {entry.resource_id ? ` / ${entry.resource_id}` : ""}
                            </span>
                          </div>
                          <small class="text-muted text-nowrap ms-2">
                            {formatTimeAgo(entry.timestamp)}
                          </small>
                        </div>
                        <div class="text-muted small mt-1 sg-fs-sm3">{entry.actor}</div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p class="text-muted small mb-0">No recent activity.</p>
                )}
              </div>
            </div>
          </div>
        )}

        <div class={isAdmin ? "col-lg-4" : "col-lg-8"}>
          <div class="card sg-card-themed h-100">
            <div class="card-body">
              <div class="d-flex justify-content-between align-items-center mb-3">
                <h6 class="mb-0">
                  <i class="bi bi-hdd-network me-2" />
                  Gateway Status
                </h6>
                <a href="/gateways" class="small text-decoration-none text-muted">
                  Manage <i class="bi bi-arrow-right" />
                </a>
              </div>
              {gateways.length > 0 ? (
                <div class="list-group list-group-flush">
                  {gateways.map((gw) => (
                    <a
                      key={gw.name}
                      href={`/gateways/${gw.name}`}
                      class="list-group-item list-group-item-action bg-transparent border-secondary px-0 py-2 d-flex justify-content-between align-items-center"
                    >
                      <span class="fw-medium">{gw.name}</span>
                      <span class={`badge ${badgeClass("gateway", gw.status ?? "offline")}`}>
                        {gw.status}
                      </span>
                    </a>
                  ))}
                </div>
              ) : (
                <div class="text-center text-muted py-3">
                  <i class="bi bi-hdd-network fs-3 d-block mb-2" />
                  <p class="small mb-2">No gateways registered.</p>
                  <p class="small mb-2">
                    Running OpenShell on this machine? Start with <code>shoreguard --local</code>{" "}
                    to auto-detect your local gateway — no certificates needed.{" "}
                    <a
                      href="https://flohofstetter.github.io/shoreguard/getting-started/solo-dev/"
                      target="_blank"
                      rel="noopener"
                    >
                      Solo-dev guide
                    </a>
                    .
                  </p>
                  {isAdmin && (
                    <div class="d-flex gap-2 justify-content-center flex-wrap">
                      <LocalGatewayCreate />
                      <a href="/gateways/new" class="btn btn-sm btn-outline-success">
                        <i class="bi bi-plus me-1" />
                        Register Gateway
                      </a>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        <div class={isAdmin ? "col-lg-3" : "col-lg-4"}>
          <NodeStatsCard />
        </div>
      </div>

      <div class="row g-3">
        {gateways.length > 0 && (
          <div class="col-md-6">
            <a
              href={gateways.length === 1 ? `/gateways/${firstGw}/wizard` : "/gateways"}
              class="btn btn-outline-success w-100 py-3"
            >
              <i class="bi bi-plus-circle me-2" />
              Create Sandbox
            </a>
          </div>
        )}
        {gateways.length > 0 && (
          <div class="col-md-6">
            <a href={sandboxesHref} class="btn btn-outline-secondary w-100 py-3">
              <i class="bi bi-grid me-2" />
              View Sandboxes
            </a>
          </div>
        )}
        {gateways.length === 0 && isAdmin && (
          <div class="col-md-6">
            <a href="/gateways/new" class="btn btn-outline-success w-100 py-3">
              <i class="bi bi-plus-circle me-2" />
              Register Gateway
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
