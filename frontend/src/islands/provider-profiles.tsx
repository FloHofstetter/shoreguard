/** Provider profile registry page (island, M37 / OpenShell PR #1170). */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth, hasRole } from "../lib/auth";
import { API, badgeClass } from "../lib/constants";
import { showConfirm, showToast } from "../lib/notify";
import { ErrorAlert, Spinner } from "../lib/widgets";

interface Profile {
  id: string;
  display_name?: string;
  category?: string;
  credentials?: unknown[];
  endpoint_count?: number;
  inference_capable?: boolean;
}

interface Diagnostic {
  severity: string;
  message: string;
  profile_id?: string;
  field?: string;
}

export default function ProviderProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [isOperator, setIsOperator] = useState(false);

  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const [lastValid, setLastValid] = useState<boolean | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await apiFetch<{ items?: Profile[] }>(`${API}/provider-profiles`);
      setProfiles(resp?.items ?? []);
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

  const parseImportText = (): unknown[] => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(importText);
    } catch (e) {
      throw new Error(`Invalid JSON: ${(e as Error).message}`);
    }
    if (!Array.isArray(parsed)) {
      throw new Error("Invalid JSON: Expected a JSON array of {profile, source} items.");
    }
    return parsed;
  };

  const lintProfiles = async () => {
    setImportBusy(true);
    setDiagnostics([]);
    setLastValid(null);
    try {
      const items = parseImportText();
      const resp = await apiFetch<{ diagnostics?: Diagnostic[]; valid?: boolean }>(
        `${API}/provider-profiles/lint`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profiles: items }),
        },
      );
      setDiagnostics(resp.diagnostics ?? []);
      setLastValid(Boolean(resp.valid));
    } catch (e) {
      setLastValid(false);
      setDiagnostics([{ severity: "error", message: (e as Error).message }]);
    } finally {
      setImportBusy(false);
    }
  };

  const applyProfiles = async () => {
    setImportBusy(true);
    try {
      const items = parseImportText();
      const resp = await apiFetch<{
        diagnostics?: Diagnostic[];
        imported?: boolean;
        profiles?: unknown[];
      }>(`${API}/provider-profiles/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profiles: items }),
      });
      setDiagnostics(resp.diagnostics ?? []);
      if (resp.imported) {
        showToast(`Imported ${(resp.profiles ?? []).length} profile(s).`, "success");
        setImportOpen(false);
        await load();
      } else {
        showToast("Import rejected — see diagnostics.", "warning");
        setLastValid(false);
      }
    } catch (e) {
      showToast(`Import failed: ${(e as Error).message}`, "danger");
    } finally {
      setImportBusy(false);
    }
  };

  const deleteProfile = async (profileId: string) => {
    const confirmed = await showConfirm(
      `Delete provider profile "${profileId}"? Custom profiles only — built-in profiles cannot be removed.`,
      { icon: "trash", iconColor: "text-danger", btnClass: "btn-danger", btnLabel: "Delete" },
    );
    if (!confirmed) return;
    try {
      const resp = await apiFetch<{ deleted?: boolean }>(`${API}/provider-profiles/${profileId}`, {
        method: "DELETE",
      });
      if (resp.deleted) {
        showToast(`Profile "${profileId}" deleted.`, "success");
        await load();
      } else {
        showToast(`Profile "${profileId}" was not removed (built-in?).`, "info");
      }
    } catch (e) {
      showToast(`Delete failed: ${(e as Error).message}`, "danger");
    }
  };

  const openImportDialog = () => {
    setImportText("");
    setDiagnostics([]);
    setLastValid(null);
    setImportOpen(true);
  };

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">
          Provider Profiles
          <span class="badge text-bg-light border ms-2 small">M37 · OpenShell PR&nbsp;#1170</span>
        </h5>
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-secondary" onClick={() => void load()} title="Refresh">
            <i class="bi bi-arrow-clockwise" />
          </button>
          {isOperator && (
            <button class="btn btn-outline-success" onClick={openImportDialog}>
              <i class="bi bi-upload me-1" />
              Import
            </button>
          )}
        </div>
      </div>

      {loading && <Spinner message="Loading profiles…" />}
      {error && <ErrorAlert message={error} />}

      {!loading && !error && profiles.length === 0 && (
        <div class="alert alert-info">
          No provider profiles registered. Set <code>providers_v2_enabled = true</code> on the
          gateway and import profiles to populate the registry.
        </div>
      )}
      {!loading && !error && profiles.length > 0 && (
        <div class="table-responsive">
          <table class="table table-sm align-middle">
            <thead>
              <tr>
                <th>ID</th>
                <th>Display name</th>
                <th>Category</th>
                <th class="text-end">Credentials</th>
                <th class="text-end">Endpoints</th>
                <th>Inference</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {profiles.map((prof) => (
                <tr key={prof.id}>
                  <td class="font-monospace">{prof.id}</td>
                  <td>{prof.display_name}</td>
                  <td>
                    <span class="badge text-bg-light border">{prof.category}</span>
                  </td>
                  <td class="text-end">{(prof.credentials ?? []).length}</td>
                  <td class="text-end">{prof.endpoint_count ?? 0}</td>
                  <td>
                    <i
                      class={`bi ${
                        prof.inference_capable
                          ? "bi-check-circle text-success"
                          : "bi-dash-circle text-muted"
                      }`}
                    />
                  </td>
                  <td class="text-end">
                    {isOperator && (
                      <button
                        class="btn btn-sm btn-outline-danger"
                        title="Delete"
                        onClick={() => void deleteProfile(prof.id)}
                      >
                        <i class="bi bi-trash" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {importOpen && (
        <div
          class="modal fade show d-block"
          tabIndex={-1}
          style="background: rgba(0,0,0,.4);"
          onClick={(e) => {
            if (e.target === e.currentTarget) setImportOpen(false);
          }}
        >
          <div class="modal-dialog modal-lg">
            <div class="modal-content">
              <div class="modal-header">
                <h6 class="modal-title">Import Provider Profiles</h6>
                <button type="button" class="btn-close" onClick={() => setImportOpen(false)} />
              </div>
              <div class="modal-body">
                <p class="small text-muted">
                  Paste a JSON array of <code>{"{profile, source}"}</code> items, then run{" "}
                  <strong>Lint</strong> to preview gateway-side diagnostics before{" "}
                  <strong>Apply</strong>.
                </p>
                <textarea
                  class="form-control font-monospace small"
                  rows={10}
                  value={importText}
                  onInput={(e) => setImportText((e.target as HTMLTextAreaElement).value)}
                  placeholder='[{"profile": {"id": "claude", "display_name": "Claude", "description": "Anthropic", "inference_capable": true}, "source": "inline"}]'
                />
                {diagnostics.length > 0 && (
                  <div class="mt-3">
                    <div class="small fw-bold mb-1">Diagnostics</div>
                    <ul class="list-group list-group-flush small">
                      {diagnostics.map((d, idx) => (
                        <li
                          key={idx}
                          class="list-group-item py-1 px-2 d-flex gap-2 align-items-baseline"
                        >
                          <span class={`badge ${badgeClass("severity", d.severity)}`}>
                            {d.severity}
                          </span>
                          <span class="font-monospace">{d.profile_id || "(global)"}</span>
                          <span class="text-muted">{d.field}</span>
                          <span>{d.message}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {lastValid !== null && (
                  <div class="mt-2 small">
                    {lastValid ? (
                      <span class="text-success">
                        <i class="bi bi-check-circle me-1" />
                        Validation passed.
                      </span>
                    ) : (
                      <span class="text-danger">
                        <i class="bi bi-exclamation-triangle me-1" />
                        Validation failed.
                      </span>
                    )}
                  </div>
                )}
              </div>
              <div class="modal-footer">
                <button class="btn btn-outline-secondary" onClick={() => setImportOpen(false)}>
                  Cancel
                </button>
                <button
                  class="btn btn-outline-primary"
                  onClick={() => void lintProfiles()}
                  disabled={importBusy}
                >
                  {importBusy && <span class="spinner-border spinner-border-sm me-1" />}
                  Lint
                </button>
                {isOperator && (
                  <button
                    class="btn btn-success"
                    onClick={() => void applyProfiles()}
                    disabled={importBusy || !lastValid}
                  >
                    Apply
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
