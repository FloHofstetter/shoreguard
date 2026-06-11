/** Gateway list page (island): registry listing, discovery, unregister. */

import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth, hasRole } from "../lib/auth";
import { badgeClass, navigateTo } from "../lib/constants";
import { formatTimeAgo } from "../lib/format";
import { showConfirm, showToast } from "../lib/notify";
import { useSortableTable } from "../lib/table";
import { ErrorAlert, Spinner } from "../lib/widgets";

export interface GatewayRow extends Record<string, unknown> {
  name: string;
  description?: string;
  endpoint?: string;
  auth_mode?: string;
  status?: string;
  version?: string;
  connected?: boolean;
  last_seen?: string;
  labels?: Record<string, string>;
}

const STATUS_ICONS: Record<string, string> = {
  connected: "circle-fill",
  running: "circle-fill",
  unreachable: "exclamation-circle",
  stopped: "stop-circle",
  offline: "circle",
};

const STATUS_LABELS: Record<string, string> = {
  connected: "Connected",
  running: "Running",
  unreachable: "Unreachable",
  stopped: "Stopped",
  offline: "Offline",
};

export function statusIcon(s: string | undefined): string {
  return STATUS_ICONS[s || "offline"] ?? "circle";
}

export function statusLabel(s: string | undefined): string {
  return STATUS_LABELS[s || "offline"] ?? (s || "offline");
}

interface DiscoverResult {
  registered: unknown[];
  skipped: unknown[];
  errors: unknown[];
}

export default function GatewaysPage() {
  const [gateways, setGateways] = useState<GatewayRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [filterLabel, setFilterLabel] = useState("");
  const [discoverResult, setDiscoverResult] = useState<DiscoverResult | null>(null);
  const [importLog, setImportLog] = useState<string[] | null>(null);
  const table = useSortableTable<GatewayRow>("name");
  const debounceRef = useRef<number | undefined>(undefined);

  const load = async (labelFilter = filterLabel) => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      for (const lbl of labelFilter.split(",")) {
        const trimmed = lbl.trim();
        if (trimmed) params.append("label", trimmed);
      }
      const qs = params.toString();
      const resp = await apiFetch<GatewayRow[] | { items?: GatewayRow[] }>(
        qs ? `/api/gateway/list?${qs}` : `/api/gateway/list`,
      );
      setGateways(Array.isArray(resp) ? resp : (resp.items ?? []));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load("");
    void ensureAuth().then(() => setIsAdmin(hasRole("admin")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onLabelInput = (value: string) => {
    setFilterLabel(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => void load(value), 300);
  };

  const discover = async () => {
    try {
      const result = await apiFetch<DiscoverResult>(`/api/gateway/discover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      setDiscoverResult(result);
      showToast(
        `Discovery: ${result.registered.length} new, ${result.skipped.length} skipped`,
        result.errors.length ? "warning" : "success",
      );
      await load();
    } catch (e) {
      showToast(`Discovery failed: ${(e as Error).message}`, "danger");
    }
  };

  const importFilesystem = async () => {
    try {
      const result = await apiFetch<{ imported: number; skipped: number; log: string[] }>(
        `/api/gateway/import-filesystem`,
        { method: "POST" },
      );
      setImportLog(result.log.length ? result.log : ["Nothing found on this machine."]);
      showToast(
        `Scan: ${result.imported} imported, ${result.skipped} skipped`,
        result.imported > 0 ? "success" : "info",
      );
      await load();
    } catch (e) {
      showToast(`Scan failed: ${(e as Error).message}`, "danger");
    }
  };

  const unregister = async (name: string) => {
    const confirmed = await showConfirm(
      `Unregister gateway "${name}"? This removes it from Shoreguard but does not affect the running gateway.`,
      { icon: "trash", iconColor: "text-danger", btnClass: "btn-danger", btnLabel: "Unregister" },
    );
    if (!confirmed) return;
    try {
      const result = await apiFetch<{ success?: boolean; error?: string }>(
        `/api/gateway/${name}`,
        { method: "DELETE" },
      );
      if (result.success) {
        showToast(`Gateway "${name}" unregistered.`, "success");
        await load();
      } else {
        showToast(`Failed: ${result.error}`, "danger");
      }
    } catch (e) {
      showToast(`Error: ${(e as Error).message}`, "danger");
    }
  };

  const visible = table.view(gateways, "name", "description", "endpoint");

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">Gateways</h5>
        <div class="btn-group btn-group-sm">
          <button
            class="btn btn-outline-secondary"
            onClick={() => void load()}
            title="Refresh"
            aria-label="Refresh"
          >
            <i class="bi bi-arrow-clockwise" />
          </button>
          {isAdmin && (
            <button
              class="btn btn-outline-primary"
              title="Run DNS-SRV / mDNS discovery"
              onClick={() => void discover()}
            >
              <i class="bi bi-broadcast me-1" />
              Discover
            </button>
          )}
          {isAdmin && (
            <button
              class="btn btn-outline-primary"
              title="Scan this machine's OpenShell config (incl. NemoClaw gateways)"
              onClick={() => void importFilesystem()}
            >
              <i class="bi bi-pc-display me-1" />
              Scan this machine
            </button>
          )}
          {isAdmin && (
            <a class="btn btn-outline-success" href="/gateways/new">
              <i class="bi bi-plus-lg me-1" />
              Register
            </a>
          )}
        </div>
      </div>

      {discoverResult && (
        <div class="alert alert-info py-2 small mb-2">
          <i class="bi bi-broadcast me-1" />
          Discovery: <strong>{discoverResult.registered.length}</strong> registered,{" "}
          <strong>{discoverResult.skipped.length}</strong> skipped,{" "}
          <strong>{discoverResult.errors.length}</strong> errors.
          <button type="button" class="btn-close float-end" onClick={() => setDiscoverResult(null)} />
        </div>
      )}

      {importLog && (
        <div class="alert alert-info py-2 small mb-2">
          <button type="button" class="btn-close float-end" onClick={() => setImportLog(null)} />
          <i class="bi bi-pc-display me-1" />
          <strong>Machine scan</strong>
          <pre class="mb-0 mt-1 small">{importLog.join("\n")}</pre>
        </div>
      )}

      {loading && <Spinner message="Loading gateways..." />}
      {error && <ErrorAlert message={error} />}

      {!loading && !error && gateways.length === 0 && !filterLabel && (
        <div class="text-center text-muted py-5">
          <i class="bi bi-hdd-network fs-1 d-block mb-3" />
          <p>No gateways registered.</p>
          <p class="small">
            Running OpenShell or NemoClaw on this machine? "Scan this machine" adopts its
            gateways automatically.
          </p>
          {isAdmin && (
            <div class="d-flex gap-2 justify-content-center">
              <button class="btn btn-primary btn-sm" onClick={() => void importFilesystem()}>
                <i class="bi bi-pc-display me-1" />
                Scan this machine
              </button>
              <a class="btn btn-success btn-sm" href="/gateways/new">
                <i class="bi bi-plus me-1" />
                Register Gateway
              </a>
            </div>
          )}
        </div>
      )}

      {!loading && !error && (
        <div class="row g-2 mb-2">
          <div class="col-md-4">
            <input
              type="text"
              class="form-control form-control-sm"
              placeholder="Filter gateways..."
              value={table.filterText}
              onInput={(e) => table.setFilterText((e.target as HTMLInputElement).value)}
            />
          </div>
          <div class="col-md-4">
            <input
              type="text"
              class="form-control form-control-sm"
              placeholder="Label filter (e.g. env:dev)"
              value={filterLabel}
              onInput={(e) => onLabelInput((e.target as HTMLInputElement).value)}
            />
          </div>
          <div class="col-md-4">
            {filterLabel && (
              <button
                class="btn btn-sm btn-outline-secondary"
                onClick={() => {
                  setFilterLabel("");
                  void load("");
                }}
              >
                <i class="bi bi-x-circle me-1" />
                Clear label filter
              </button>
            )}
          </div>
        </div>
      )}

      {!loading && !error && gateways.length > 0 && (
        <div class="table-responsive mb-4">
          <table class="table table-striped table-hover table-sm align-middle table-clickable">
            <thead>
              <tr>
                <th class={table.sortClass("name")} onClick={() => table.sortBy("name")}>
                  Name
                </th>
                <th
                  class={`d-none d-lg-table-cell ${table.sortClass("description")}`}
                  onClick={() => table.sortBy("description")}
                >
                  Description
                </th>
                <th>Endpoint</th>
                <th>Auth</th>
                <th class={table.sortClass("status")} onClick={() => table.sortBy("status")}>
                  Status
                </th>
                <th
                  class={`d-none d-md-table-cell ${table.sortClass("last_seen")}`}
                  onClick={() => table.sortBy("last_seen")}
                >
                  Last Seen
                </th>
                <th class="text-end sg-w-60" />
              </tr>
            </thead>
            <tbody>
              {visible.map((gw) => (
                <tr
                  key={gw.name}
                  class={`sg-cursor-pointer ${gw.connected ? "table-active" : ""}`}
                  onClick={() => navigateTo(`/gateways/${gw.name}`)}
                >
                  <td>
                    <strong>{gw.name}</strong>
                  </td>
                  <td class="d-none d-lg-table-cell small text-muted text-truncate sg-mw-200">
                    {gw.description || "—"}
                  </td>
                  <td class="font-monospace small">{gw.endpoint || "—"}</td>
                  <td class="small">{gw.auth_mode || "—"}</td>
                  <td>
                    <span class={`badge ${badgeClass("gateway", gw.status ?? "offline")}`}>
                      <i class={`bi me-1 bi-${statusIcon(gw.status)}`} />
                      <span>{statusLabel(gw.status)}</span>
                    </span>
                    {gw.status === "connected" && gw.version && (
                      <span class="text-muted small ms-1">{gw.version}</span>
                    )}
                  </td>
                  <td class="d-none d-md-table-cell small text-muted">
                    {gw.last_seen ? formatTimeAgo(gw.last_seen) : "—"}
                  </td>
                  <td class="text-end" onClick={(e) => e.stopPropagation()}>
                    {isAdmin && (
                      <button
                        class="btn btn-sm text-muted"
                        title="Unregister"
                        onClick={() => void unregister(gw.name)}
                      >
                        <i class="bi bi-trash3" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
