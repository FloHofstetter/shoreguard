/** Dashboard page (island): stat cards, recent activity, gateway status. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { auth, ensureAuth, hasRole } from "../lib/auth";
import { API, badgeClass, GW, gwUrl } from "../lib/constants";
import { formatTimeAgo } from "../lib/format";
import { ErrorAlert, Spinner } from "../lib/widgets";

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

export default function DashboardPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [gateways, setGateways] = useState<GatewayItem[]>([]);
  const [sandboxCount, setSandboxCount] = useState(0);
  const [presetCount, setPresetCount] = useState(0);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  // Pending approvals card — populated by future aggregation; shown as 0.
  const approvalCount = 0;

  useEffect(() => {
    (async () => {
      await ensureAuth();
      try {
        const [gwData, presets] = await Promise.all([
          apiFetch<{ items?: GatewayItem[] }>(`/api/gateway/list`).catch(() => null),
          apiFetch<unknown[]>(`/api/policies/presets`).catch(() => null),
        ]);
        setGateways(gwData?.items ?? []);
        setPresetCount((presets ?? []).length);

        if (GW) {
          try {
            const sbs = await apiFetch<unknown[] | { items?: unknown[] }>(`${API}/sandboxes`);
            const items = Array.isArray(sbs) ? sbs : (sbs?.items ?? []);
            setSandboxCount(items.length);
          } catch {
            // no gateway
          }
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

  return (
    <div class="sg-fade-in">
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
          <a href={GW ? gwUrl("/sandboxes") : "/gateways"} class="text-decoration-none">
            <div class="card sg-card-themed h-100">
              <div class="card-body">
                <div class="d-flex align-items-center mb-2">
                  <i class="bi bi-grid text-success me-2" />
                  <span class="small text-muted">Sandboxes</span>
                </div>
                <div class="fs-2 fw-bold">{sandboxCount}</div>
                <span class="text-muted small">{GW ? `on ${GW}` : "select a gateway"}</span>
              </div>
            </div>
          </a>
        </div>

        {GW && (
          <div class="col-6 col-lg-3">
            <a href={gwUrl("/sandboxes")} class="text-decoration-none">
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
        )}

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

      <div class="row g-3 mb-4">
        {isAdmin && (
          <div class="col-lg-7">
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

        <div class={isAdmin ? "col-lg-5" : "col-12"}>
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
                    <a href="/gateways/new" class="btn btn-sm btn-outline-success">
                      <i class="bi bi-plus me-1" />
                      Register Gateway
                    </a>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <div class="row g-3">
        {GW && (
          <div class="col-md-6">
            <a href={gwUrl("/wizard")} class="btn btn-outline-success w-100 py-3">
              <i class="bi bi-plus-circle me-2" />
              Create Sandbox
            </a>
          </div>
        )}
        {GW && (
          <div class="col-md-6">
            <a href={gwUrl("/sandboxes")} class="btn btn-outline-secondary w-100 py-3">
              <i class="bi bi-grid me-2" />
              View Sandboxes
            </a>
          </div>
        )}
        {!GW && isAdmin && (
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
