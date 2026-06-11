/** Sandbox list page (island). */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth, hasRole } from "../lib/auth";
import { API, badgeClass, gwUrl, navigateTo } from "../lib/constants";
import { showConfirm, showToast } from "../lib/notify";
import { Spinner } from "../lib/widgets";

export interface Sandbox {
  id: string;
  name: string;
  phase: string;
  image?: string;
  description?: string;
  labels?: Record<string, string>;
  gpu?: boolean;
  current_policy_version?: number;
  created_at_ms?: number;
}

export async function deleteSandbox(name: string): Promise<boolean> {
  const confirmed = await showConfirm(`Delete sandbox "${name}"? This cannot be undone.`, {
    icon: "trash",
    iconColor: "text-danger",
    btnClass: "btn-danger",
    btnLabel: "Delete",
  });
  if (!confirmed) return false;
  try {
    await apiFetch(`${API}/sandboxes/${name}`, { method: "DELETE" });
    showToast(`Sandbox "${name}" deleted.`, "success");
    return true;
  } catch (e) {
    showToast(`Delete failed: ${(e as Error).message}`, "danger");
    return false;
  }
}

export default function SandboxListPage() {
  const [sandboxes, setSandboxes] = useState<Sandbox[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [isOperator, setIsOperator] = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await apiFetch<Sandbox[] | { items?: Sandbox[] }>(`${API}/sandboxes`);
      setSandboxes(Array.isArray(resp) ? resp : (resp.items ?? []));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    void ensureAuth().then(() => setIsOperator(hasRole("operator")));
  }, []);

  const handleDelete = async (name: string) => {
    if (await deleteSandbox(name)) await load();
  };

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">Sandboxes</h5>
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-secondary" onClick={() => void load()} title="Refresh">
            <i class="bi bi-arrow-clockwise" />
          </button>
          <a class="btn btn-outline-success" href={gwUrl("/wizard")}>
            <i class="bi bi-plus-lg me-1" />
            New
          </a>
        </div>
      </div>

      {loading && <Spinner message="Loading sandboxes..." />}

      {!loading && error && (
        <div class="text-center text-muted py-5">
          <i class="bi bi-exclamation-triangle fs-1 d-block mb-3 text-warning" />
          <p>Could not load sandboxes.</p>
          <p class="small">{error}</p>
        </div>
      )}

      {!loading && !error && sandboxes.length === 0 && (
        <div class="text-center text-muted py-5">
          <i class="bi bi-inbox fs-1 d-block mb-3" />
          <p>No sandboxes running.</p>
          <button
            class="btn btn-outline-success btn-sm"
            onClick={() => navigateTo(gwUrl("/wizard"))}
          >
            <i class="bi bi-plus-circle me-1" />
            Create Sandbox
          </button>
        </div>
      )}

      {!loading && !error && sandboxes.length > 0 && (
        <div class="table-responsive">
          <table class="table table-striped table-hover table-sm align-middle table-clickable">
            <thead>
              <tr>
                <th>Name</th>
                <th class="d-none d-lg-table-cell">Description</th>
                <th class="d-none d-md-table-cell">Image</th>
                <th>Phase</th>
                <th class="d-none d-md-table-cell">Policy</th>
                <th class="d-none d-lg-table-cell">GPU</th>
                <th class="text-end d-none d-md-table-cell">Created</th>
                <th class="text-end">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sandboxes.map((sb) => (
                <tr
                  key={sb.name}
                  class="sg-cursor-pointer"
                  onClick={() => navigateTo(gwUrl(`/sandboxes/${sb.name}`))}
                >
                  <td>
                    <strong>{sb.name}</strong>
                    {sb.labels && Object.keys(sb.labels).length > 0 && (
                      <div class="mt-1">
                        {Object.entries(sb.labels).map(([key, val]) => (
                          <span
                            key={key}
                            class="badge text-bg-light border font-monospace me-1 sg-fs-xs"
                          >
                            {key}={val}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td class="d-none d-lg-table-cell small text-muted text-truncate sg-mw-200">
                    {sb.description || "—"}
                  </td>
                  <td class="d-none d-md-table-cell">
                    <span class="font-monospace small cell-truncate" title={sb.image || ""}>
                      {sb.image || "Default"}
                    </span>
                  </td>
                  <td>
                    <span class={`badge ${badgeClass("phase", sb.phase)}`}>{sb.phase}</span>
                  </td>
                  <td class="d-none d-md-table-cell">
                    <span class="badge text-bg-secondary">v{sb.current_policy_version}</span>
                  </td>
                  <td class="d-none d-lg-table-cell">
                    {sb.gpu ? (
                      <i class="bi bi-gpu-card text-info" />
                    ) : (
                      <span class="text-muted">—</span>
                    )}
                  </td>
                  <td class="text-end small text-muted d-none d-md-table-cell">
                    {sb.created_at_ms ? new Date(sb.created_at_ms).toLocaleString() : ""}
                  </td>
                  <td class="text-end" onClick={(e) => e.stopPropagation()}>
                    {isOperator && (
                      <button
                        class="btn btn-sm text-muted"
                        title="Delete"
                        onClick={() => void handleDelete(sb.name)}
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
