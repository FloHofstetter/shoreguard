/** Audit log page (island): filterable listing + CSV/JSON export. */

import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { showToast } from "../lib/notify";
import { badgeClass } from "../lib/constants";
import { EmptyState, ErrorAlert, Spinner } from "../lib/widgets";

interface AuditEntry {
  id: number;
  timestamp?: string;
  actor?: string;
  actor_role?: string;
  action?: string;
  resource_type?: string;
  resource_id?: string;
  gateway?: string;
  client_ip?: string;
}

const RESOURCE_TYPES = [
  ["", "All resources"],
  ["gateway", "Gateway"],
  ["sandbox", "Sandbox"],
  ["policy", "Policy"],
  ["approval", "Approval"],
  ["provider", "Provider"],
  ["user", "User"],
  ["service_principal", "Service Principal"],
  ["inference", "Inference"],
] as const;

function formatTs(iso: string | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "";
}

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filterActor, setFilterActor] = useState("");
  const [filterAction, setFilterAction] = useState("");
  const [filterResourceType, setFilterResourceType] = useState("");
  const [filterGateway, setFilterGateway] = useState("");
  const debounceTimer = useRef<number | undefined>(undefined);

  const buildParams = (extra?: Record<string, string>) => {
    const params = new URLSearchParams(extra);
    if (filterActor) params.set("actor", filterActor);
    if (filterAction) params.set("action", filterAction);
    if (filterResourceType) params.set("resource_type", filterResourceType);
    if (filterGateway) params.set("gateway", filterGateway);
    return params;
  };

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const params = buildParams({ limit: "1000" });
      const resp = await apiFetch<
        AuditEntry[] | { entries?: AuditEntry[]; items?: AuditEntry[] }
      >(`/api/audit?${params}`);
      setEntries(Array.isArray(resp) ? resp : (resp.entries ?? resp.items ?? []));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    clearTimeout(debounceTimer.current);
    debounceTimer.current = window.setTimeout(() => void load(), 300);
    return () => clearTimeout(debounceTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterActor, filterAction, filterResourceType, filterGateway]);

  const hasFilters = Boolean(filterActor || filterAction || filterResourceType || filterGateway);

  const clearFilters = () => {
    setFilterActor("");
    setFilterAction("");
    setFilterResourceType("");
    setFilterGateway("");
  };

  const exportAudit = (format: "csv" | "json") => {
    window.open(`/api/audit/export?${buildParams({ format })}`, "_blank");
  };

  const verifyChain = async () => {
    try {
      const r = await apiFetch<{
        ok: boolean;
        checked: number;
        legacy: number;
        first_bad_id: number | null;
      }>(`/api/audit/verify`);
      if (r.ok) {
        const legacy = r.legacy ? ` (${r.legacy} pre-chain entries skipped)` : "";
        showToast(`Hash chain intact — ${r.checked} entries verified${legacy}.`, "success");
      } else {
        showToast(`HASH CHAIN BROKEN at entry ${r.first_bad_id}.`, "danger");
      }
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-4">
        <h5 class="mb-0">
          <i class="bi bi-journal-text me-2" />
          Audit Log
        </h5>
        <div class="d-flex gap-2">
          <button
            class="btn btn-sm btn-outline-secondary"
            title="Verify the tamper-evidence hash chain"
            onClick={() => void verifyChain()}
          >
            <i class="bi bi-shield-check me-1" />
            Verify chain
          </button>
          <button class="btn btn-sm btn-outline-secondary" onClick={() => exportAudit("csv")}>
            <i class="bi bi-filetype-csv me-1" />
            Export CSV
          </button>
          <button class="btn btn-sm btn-outline-secondary" onClick={() => exportAudit("json")}>
            <i class="bi bi-filetype-json me-1" />
            Export JSON
          </button>
        </div>
      </div>

      <div class="row g-2 mb-3">
        <div class="col-md-3">
          <input
            type="text"
            class="form-control form-control-sm"
            placeholder="Actor"
            value={filterActor}
            onInput={(e) => setFilterActor((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="col-md-2">
          <select
            class="form-select form-select-sm"
            value={filterResourceType}
            onChange={(e) => setFilterResourceType((e.target as HTMLSelectElement).value)}
          >
            {RESOURCE_TYPES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div class="col-md-3">
          <input
            type="text"
            class="form-control form-control-sm"
            placeholder="Action (e.g. sandbox.create)"
            value={filterAction}
            onInput={(e) => setFilterAction((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="col-md-2">
          <input
            type="text"
            class="form-control form-control-sm"
            placeholder="Gateway"
            value={filterGateway}
            onInput={(e) => setFilterGateway((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="col-md-2">
          {hasFilters && (
            <button class="btn btn-sm btn-outline-secondary" onClick={clearFilters}>
              <i class="bi bi-x-circle me-1" />
              Clear
            </button>
          )}
        </div>
      </div>

      {loading && <Spinner message="Loading audit log..." />}
      {error && <ErrorAlert message={error} />}
      {!loading && !error && entries.length === 0 && (
        <EmptyState icon="journal" message="No audit entries found." />
      )}
      {!loading && !error && entries.length > 0 && (
        <div class="table-responsive">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Actor</th>
                <th>Role</th>
                <th>Action</th>
                <th>Resource</th>
                <th>ID</th>
                <th>Gateway</th>
                <th>IP</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td class="text-muted small text-nowrap">{formatTs(entry.timestamp)}</td>
                  <td>{entry.actor}</td>
                  <td>
                    <span class={`badge ${badgeClass("role", entry.actor_role)}`}>
                      {entry.actor_role}
                    </span>
                  </td>
                  <td>
                    <code>{entry.action}</code>
                  </td>
                  <td>
                    <span class="badge text-bg-dark">{entry.resource_type}</span>
                  </td>
                  <td class="font-monospace small">{entry.resource_id || "—"}</td>
                  <td class="text-muted small">{entry.gateway || "—"}</td>
                  <td class="text-muted small">{entry.client_ip || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
