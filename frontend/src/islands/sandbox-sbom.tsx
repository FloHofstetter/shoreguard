/** SBOM viewer (island): CycloneDX snapshot, components, vulnerabilities. */

import { useEffect, useRef, useState } from "preact/hooks";

import { ensureAuth, hasRole } from "../lib/auth";
import { GW } from "../lib/constants";
import { showConfirm, showToast } from "../lib/notify";
import { Spinner } from "../lib/widgets";

const SEVERITY_BADGE: Record<string, string> = {
  CRITICAL: "bg-danger",
  HIGH: "bg-warning text-dark",
  MEDIUM: "bg-info text-dark",
  LOW: "bg-secondary",
  INFO: "bg-light text-dark",
  UNKNOWN: "bg-light text-dark",
  CLEAN: "bg-success",
};

const SEVERITY_BTN: Record<string, string> = {
  CRITICAL: "btn-danger",
  HIGH: "btn-warning",
  MEDIUM: "btn-info",
  LOW: "btn-secondary",
  CLEAN: "btn-success",
};

const SEVERITY_FILTERS = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "CLEAN"];
const PAGE_LIMIT = 50;

interface Snapshot {
  bom_format?: string;
  spec_version?: string;
  uploaded_at?: string;
  uploaded_by?: string;
  component_count?: number;
  vulnerability_count?: number;
  max_severity?: string | null;
}

interface Component {
  id: number;
  name: string;
  version?: string;
  type?: string;
  purl?: string;
  licenses?: string;
  vuln_count: number;
  max_severity?: string;
}

interface Vulnerability {
  id: string;
  severity?: string;
  cvss_score?: number | null;
  description?: string;
  affects?: string[];
  references?: string[];
}

function severityBadge(sev: string | null | undefined): string {
  if (!sev) return SEVERITY_BADGE.CLEAN;
  return SEVERITY_BADGE[sev] ?? "bg-secondary";
}

function formatTime(iso: string | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function SandboxSbomPage({ name }: { name: string }) {
  const baseUrl = `/api/gateways/${encodeURIComponent(GW)}/sandboxes/${encodeURIComponent(name)}/sbom`;
  const [loading, setLoading] = useState(true);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [tab, setTab] = useState<"components" | "vulnerabilities">("components");
  const [components, setComponents] = useState<Component[]>([]);
  const [vulns, setVulns] = useState<Vulnerability[] | null>(null);
  const [vulnsLoading, setVulnsLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [isAdmin, setIsAdmin] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<number | undefined>(undefined);

  const loadComponents = async (opts: { offset?: number; search?: string; severity?: string } = {}) => {
    const params = new URLSearchParams({
      offset: String(opts.offset ?? offset),
      limit: String(PAGE_LIMIT),
    });
    const s = opts.search ?? search;
    const sev = opts.severity ?? severity;
    if (s) params.set("search", s);
    if (sev) params.set("severity", sev);
    try {
      const resp = await fetch(`${baseUrl}/components?${params}`);
      if (resp.ok) {
        const data = await resp.json();
        setComponents(data.items ?? []);
        setTotal(data.total ?? 0);
      }
    } catch (e) {
      console.error("Failed to load components:", e);
    }
  };

  const loadSnapshot = async () => {
    setLoading(true);
    try {
      const resp = await fetch(baseUrl);
      if (resp.status === 404 || !resp.ok) {
        setSnapshot(null);
        return;
      }
      setSnapshot(await resp.json());
      await loadComponents({ offset: 0 });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadSnapshot();
    void ensureAuth().then(() => setIsAdmin(hasRole("admin")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const loadVulns = async () => {
    if (!snapshot || vulns !== null) return;
    setVulnsLoading(true);
    try {
      const resp = await fetch(`${baseUrl}/vulnerabilities`);
      if (resp.ok) {
        const data = await resp.json();
        setVulns(data.vulnerabilities ?? []);
      }
    } catch (e) {
      console.error("Failed to load vulnerabilities:", e);
    } finally {
      setVulnsLoading(false);
    }
  };

  const upload = async (rawJson: string) => {
    setLoading(true);
    try {
      const resp = await fetch(baseUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: rawJson,
      });
      if (!resp.ok) {
        let msg = "Upload failed";
        try {
          const err = await resp.json();
          msg = err.detail || msg;
        } catch {
          // ignore
        }
        showToast(`SBOM upload failed: ${msg}`, "danger");
        return;
      }
      setVulns(null);
      setOffset(0);
      setSearch("");
      setSeverity("");
      await loadSnapshot();
    } finally {
      setLoading(false);
    }
  };

  const uploadFromInput = async (event: Event) => {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const text = await file.text();
    input.value = "";
    await upload(text);
  };

  const confirmDelete = async () => {
    const confirmed = await showConfirm("Delete the SBOM snapshot for this sandbox?", {
      icon: "trash3",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!confirmed) return;
    const resp = await fetch(baseUrl, { method: "DELETE" });
    if (resp.ok || resp.status === 204) {
      setSnapshot(null);
      setComponents([]);
      setVulns(null);
      setTotal(0);
    } else {
      showToast("Failed to delete SBOM", "danger");
    }
  };

  const onSearchInput = (value: string) => {
    setSearch(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      setOffset(0);
      void loadComponents({ offset: 0, search: value });
    }, 300);
  };

  const setSeverityFilter = (sev: string) => {
    const next = severity === sev ? "" : sev;
    setSeverity(next);
    setOffset(0);
    void loadComponents({ offset: 0, severity: next });
  };

  const paginate = (newOffset: number) => {
    setOffset(newOffset);
    void loadComponents({ offset: newOffset });
  };

  const curlExample = `curl -X POST "${window.location.origin}${baseUrl}" \\\n  -H "Authorization: Bearer $SHOREGUARD_TOKEN" \\\n  -H "Content-Type: application/json" \\\n  --data-binary @sbom.cdx.json`;

  if (loading && !snapshot) return <Spinner message="Loading SBOM…" />;

  if (!snapshot) {
    return (
      <div class="text-center py-5">
        <i class="bi bi-box-seam fs-1 text-muted" />
        <h5 class="mt-3">No SBOM uploaded</h5>
        <p class="text-muted">
          Upload a CycloneDX JSON SBOM from your CI pipeline to see components, licenses, and
          known vulnerabilities for this sandbox.
        </p>
        <div class="mx-auto mt-3" style="max-width: 720px;">
          <p class="small text-muted mb-2">From CI:</p>
          <pre class="bg-light p-3 small text-start">
            <code>{curlExample}</code>
          </pre>
        </div>
        {isAdmin && (
          <button class="btn btn-primary mt-2" onClick={() => fileInputRef.current?.click()}>
            <i class="bi bi-upload me-1" />
            Upload CycloneDX JSON
          </button>
        )}
        <input
          type="file"
          ref={fileInputRef}
          accept="application/json,.json,.cdx.json"
          class="d-none"
          onChange={(e) => void uploadFromInput(e)}
        />
      </div>
    );
  }

  return (
    <div>
      <div class="card mb-3">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-start flex-wrap gap-2">
            <div>
              <h5 class="card-title mb-1">
                <i class="bi bi-box-seam me-1" />
                SBOM Snapshot
                <span class={`badge ms-2 ${severityBadge(snapshot.max_severity)}`}>
                  {snapshot.max_severity || "CLEAN"}
                </span>
              </h5>
              <div class="text-muted small">
                <span>{snapshot.bom_format}</span> <span>{snapshot.spec_version}</span> · uploaded{" "}
                <span>{formatTime(snapshot.uploaded_at)}</span> by{" "}
                <span class="font-monospace">{snapshot.uploaded_by}</span>
              </div>
              <div class="small mt-1">
                <span class="me-3">
                  <strong>{snapshot.component_count}</strong> components
                </span>
                <span>
                  <strong>{snapshot.vulnerability_count}</strong> vulnerabilities
                </span>
              </div>
            </div>
            <div class="d-flex gap-2 flex-wrap">
              <button
                class="btn btn-outline-secondary btn-sm"
                onClick={() => window.location.assign(`${baseUrl}/raw`)}
              >
                <i class="bi bi-download me-1" />
                Download
              </button>
              {isAdmin && (
                <button
                  class="btn btn-outline-primary btn-sm"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <i class="bi bi-arrow-repeat me-1" />
                  Replace
                </button>
              )}
              {isAdmin && (
                <button class="btn btn-outline-danger btn-sm" onClick={() => void confirmDelete()}>
                  <i class="bi bi-trash3 me-1" />
                  Delete
                </button>
              )}
              <input
                type="file"
                ref={fileInputRef}
                accept="application/json,.json,.cdx.json"
                class="d-none"
                onChange={(e) => void uploadFromInput(e)}
              />
            </div>
          </div>
        </div>
      </div>

      <ul class="nav nav-tabs mb-3">
        <li class="nav-item">
          <a
            class={`nav-link ${tab === "components" ? "active" : ""}`}
            href="#"
            onClick={(e) => {
              e.preventDefault();
              setTab("components");
            }}
          >
            Components <span class="badge bg-secondary ms-1">{snapshot.component_count}</span>
          </a>
        </li>
        <li class="nav-item">
          <a
            class={`nav-link ${tab === "vulnerabilities" ? "active" : ""}`}
            href="#"
            onClick={(e) => {
              e.preventDefault();
              void loadVulns();
              setTab("vulnerabilities");
            }}
          >
            Vulnerabilities{" "}
            <span class="badge bg-danger ms-1">{snapshot.vulnerability_count}</span>
          </a>
        </li>
      </ul>

      {tab === "components" && (
        <div>
          <div class="d-flex gap-2 mb-2 flex-wrap align-items-center">
            <input
              type="text"
              class="form-control form-control-sm"
              style="max-width: 280px;"
              placeholder="Search name or purl…"
              value={search}
              onInput={(e) => onSearchInput((e.target as HTMLInputElement).value)}
            />
            <span class="text-muted small ms-2">Severity:</span>
            {SEVERITY_FILTERS.map((sev) => (
              <button
                key={sev}
                type="button"
                class={`btn btn-sm ${
                  severity === sev ? (SEVERITY_BTN[sev] ?? "btn-secondary") : "btn-outline-secondary"
                }`}
                onClick={() => setSeverityFilter(sev)}
              >
                {sev}
              </button>
            ))}
            {(search || severity) && (
              <button
                type="button"
                class="btn btn-sm btn-link ms-auto"
                onClick={() => {
                  setSearch("");
                  setSeverity("");
                  setOffset(0);
                  void loadComponents({ offset: 0, search: "", severity: "" });
                }}
              >
                Clear filters
              </button>
            )}
          </div>

          <div class="table-responsive">
            <table class="table table-sm table-hover align-middle">
              <thead>
                <tr>
                  <th>Name</th>
                  <th style="width:120px">Version</th>
                  <th style="width:90px">Type</th>
                  <th>License</th>
                  <th style="width:120px">Vulnerabilities</th>
                </tr>
              </thead>
              <tbody>
                {components.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <span class="font-monospace small">{c.name}</span>
                      {c.purl && (
                        <div
                          class="text-muted small text-truncate"
                          style="max-width: 360px;"
                          title={c.purl}
                        >
                          {c.purl}
                        </div>
                      )}
                    </td>
                    <td class="font-monospace small">{c.version || "—"}</td>
                    <td>
                      <span class="badge bg-light text-dark">{c.type || "—"}</span>
                    </td>
                    <td class="small">{c.licenses || "—"}</td>
                    <td>
                      {c.vuln_count > 0 ? (
                        <span class={`badge ${severityBadge(c.max_severity)}`}>
                          {c.vuln_count} · {c.max_severity}
                        </span>
                      ) : (
                        <span class="text-success small">
                          <i class="bi bi-check-circle" />
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {components.length === 0 && !loading && (
            <div class="text-muted text-center py-3">No components match the current filters.</div>
          )}

          {total > PAGE_LIMIT && (
            <div class="d-flex justify-content-between align-items-center mt-2">
              <div class="text-muted small">
                Showing <strong>{offset + 1}</strong>–
                <strong>{Math.min(offset + PAGE_LIMIT, total)}</strong> of <strong>{total}</strong>
              </div>
              <div class="btn-group btn-group-sm">
                <button
                  class="btn btn-outline-secondary"
                  disabled={offset === 0}
                  onClick={() => paginate(Math.max(0, offset - PAGE_LIMIT))}
                >
                  <i class="bi bi-chevron-left" /> Prev
                </button>
                <button
                  class="btn btn-outline-secondary"
                  disabled={offset + PAGE_LIMIT >= total}
                  onClick={() => paginate(offset + PAGE_LIMIT)}
                >
                  Next <i class="bi bi-chevron-right" />
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "vulnerabilities" && (
        <div>
          {vulnsLoading && (
            <div class="text-muted text-center py-3">
              <div class="spinner-border spinner-border-sm me-2" />
              Loading vulnerabilities…
            </div>
          )}
          {!vulnsLoading && (vulns ?? []).length === 0 && (
            <div class="text-success text-center py-4">
              <i class="bi bi-shield-check fs-3" />
              <p class="mt-2 mb-0">No vulnerabilities declared in this SBOM.</p>
            </div>
          )}
          {!vulnsLoading &&
            (vulns ?? []).map((v) => (
              <div key={v.id} class="card mb-2">
                <div class="card-body py-2">
                  <div class="d-flex justify-content-between align-items-start gap-2">
                    <div>
                      <span class={`badge me-2 ${severityBadge(v.severity)}`}>{v.severity}</span>
                      <span class="font-monospace fw-bold">{v.id}</span>
                      {v.cvss_score !== null && v.cvss_score !== undefined && (
                        <span class="text-muted small ms-2">CVSS {v.cvss_score}</span>
                      )}
                    </div>
                  </div>
                  {v.description && <p class="small text-muted mt-1 mb-1">{v.description}</p>}
                  {(v.affects ?? []).length > 0 && (
                    <div class="small">
                      <span class="text-muted">Affects:</span>
                      {v.affects!.map((ref) => (
                        <span key={ref} class="font-monospace ms-1">
                          {ref}
                        </span>
                      ))}
                    </div>
                  )}
                  {(v.references ?? []).length > 0 && (
                    <div class="small">
                      {v.references!.map((url) => (
                        <a key={url} class="me-2" href={url} target="_blank" rel="noopener">
                          {url}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
