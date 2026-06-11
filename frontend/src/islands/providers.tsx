/** Provider management (islands): list + create/edit form + refresh modal. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth, hasRole } from "../lib/auth";
import { API, navigateTo } from "../lib/constants";
import { Modal } from "../lib/Modal";
import { showConfirm, showToast } from "../lib/notify";
import { EmptyState, ErrorAlert, Spinner } from "../lib/widgets";

interface ProviderType {
  type: string;
  label?: string;
  icon?: string;
  cred_key?: string;
}

interface Provider {
  name: string;
  type: string;
  credentials?: Record<string, string>;
  config?: Record<string, string>;
}

interface DetectedServer {
  label: string;
  base_url: string;
  provider_type: string;
  suggested_name: string;
  models?: string[];
}

const REFRESH_STRATEGIES = [
  "static",
  "external",
  "oauth2_refresh_token",
  "oauth2_client_credentials",
  "google_service_account_jwt",
];

interface RefreshCredential {
  credential_key: string;
  strategy: string;
  status?: string;
  last_error?: string;
  expires_at_ms?: number;
  next_refresh_at_ms?: number;
}

let providerTypesCache: Record<string, ProviderType> | null = null;

async function loadProviderTypes(): Promise<Record<string, ProviderType>> {
  if (providerTypesCache) return providerTypesCache;
  try {
    const types = await apiFetch<ProviderType[]>(`${API}/providers/types`);
    providerTypesCache = Object.fromEntries(types.map((t) => [t.type, t]));
  } catch {
    providerTypesCache = {};
  }
  return providerTypesCache;
}

function parseKeyValueLines(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  if (!text?.trim()) return result;
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const idx = trimmed.indexOf("=");
    if (idx > 0) {
      result[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
    }
  }
  return result;
}

function formatTimestamp(ms: number | undefined): string {
  return ms ? new Date(ms).toLocaleString() : "—";
}

// ── Credential refresh modal ─────────────────────────────────────────

function ProviderRefreshModal({ provider, onClose }: { provider: string; onClose: () => void }) {
  const [credentials, setCredentials] = useState<RefreshCredential[] | null>(null);
  const [error, setError] = useState("");
  const [key, setKey] = useState("");
  const [strategy, setStrategy] = useState("static");
  const [material, setMaterial] = useState("");
  const [secretKeys, setSecretKeys] = useState("");

  const reload = async () => {
    try {
      const resp = await apiFetch<{ credentials?: RefreshCredential[] }>(
        `${API}/providers/${provider}/refresh`,
      );
      setCredentials(resp?.credentials ?? []);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const submitConfig = async (e: Event) => {
    e.preventDefault();
    const credential_key = key.trim();
    if (!credential_key) return;
    try {
      await apiFetch(`${API}/providers/${provider}/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          credential_key,
          strategy,
          material: parseKeyValueLines(material),
          secret_material_keys: secretKeys
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        }),
      });
      showToast(`Refresh configured for "${credential_key}".`, "success");
      setKey("");
      setMaterial("");
      setSecretKeys("");
      await reload();
    } catch (err) {
      showToast(`Configure failed: ${(err as Error).message}`, "danger");
    }
  };

  const rotate = async (credentialKey: string) => {
    try {
      await apiFetch(`${API}/providers/${provider}/refresh/rotate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential_key: credentialKey }),
      });
      showToast(`Credential "${credentialKey}" rotated.`, "success");
      await reload();
    } catch (e) {
      showToast(`Rotation failed: ${(e as Error).message}`, "danger");
    }
  };

  const removeRefresh = async (credentialKey: string) => {
    const confirmed = await showConfirm(`Remove refresh configuration for "${credentialKey}"?`, {
      icon: "trash3",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Remove",
    });
    if (!confirmed) return;
    try {
      await apiFetch(
        `${API}/providers/${provider}/refresh?credential_key=${encodeURIComponent(credentialKey)}`,
        { method: "DELETE" },
      );
      showToast(`Refresh removed for "${credentialKey}".`, "success");
      await reload();
    } catch (e) {
      showToast(`Delete failed: ${(e as Error).message}`, "danger");
    }
  };

  return (
    <Modal
      size="lg"
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-arrow-repeat me-2" />
          Credential Refresh — <span class="font-monospace">{provider}</span>
        </span>
      }
    >
      <div class="mb-4">
        {error && <ErrorAlert message={error} />}
        {!error && credentials === null && <Spinner message="Loading refresh status…" />}
        {!error && credentials !== null && credentials.length === 0 && (
          <p class="text-muted small mb-0">
            <i class="bi bi-info-circle me-1" />
            No refresh configured for this provider yet.
          </p>
        )}
        {!error && credentials !== null && credentials.length > 0 && (
          <div class="table-responsive">
            <table class="table table-sm align-middle mb-0">
              <thead>
                <tr>
                  <th>Credential</th>
                  <th>Strategy</th>
                  <th>Status</th>
                  <th>Expires</th>
                  <th>Next refresh</th>
                  <th class="text-end">Actions</th>
                </tr>
              </thead>
              <tbody>
                {credentials.map((c) => (
                  <tr key={c.credential_key}>
                    <td class="font-monospace small">
                      <strong>{c.credential_key}</strong>
                    </td>
                    <td>
                      <span class="badge text-bg-secondary">{c.strategy}</span>
                    </td>
                    <td class="small">
                      {c.status || "—"}
                      {c.last_error && (
                        <i
                          class="bi bi-exclamation-triangle-fill text-danger ms-1"
                          title={c.last_error}
                        />
                      )}
                    </td>
                    <td class="small">{formatTimestamp(c.expires_at_ms)}</td>
                    <td class="small">{formatTimestamp(c.next_refresh_at_ms)}</td>
                    <td class="text-end">
                      <button
                        class="btn btn-sm text-muted"
                        title="Rotate now"
                        onClick={() => void rotate(c.credential_key)}
                      >
                        <i class="bi bi-arrow-repeat" />
                      </button>
                      <button
                        class="btn btn-sm text-muted"
                        title="Remove refresh"
                        onClick={() => void removeRefresh(c.credential_key)}
                      >
                        <i class="bi bi-trash3" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <hr />
      <h6 class="text-muted mb-3">
        <i class="bi bi-sliders me-1" />
        Configure refresh
      </h6>
      <form class="row g-2" onSubmit={(e) => void submitConfig(e)}>
        <div class="col-md-6">
          <label class="form-label small">Credential key</label>
          <input
            class="form-control form-control-sm"
            required
            placeholder="e.g. ANTHROPIC_API_KEY"
            value={key}
            onInput={(e) => setKey((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="col-md-6">
          <label class="form-label small">Strategy</label>
          <select
            class="form-select form-select-sm"
            value={strategy}
            onChange={(e) => setStrategy((e.target as HTMLSelectElement).value)}
          >
            {REFRESH_STRATEGIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div class="col-12">
          <label class="form-label small">
            Material <span class="text-muted">(one <code>key=value</code> per line)</span>
          </label>
          <textarea
            class="form-control form-control-sm font-monospace"
            rows={3}
            placeholder={"token_url=https://...\nclient_id=..."}
            value={material}
            onInput={(e) => setMaterial((e.target as HTMLTextAreaElement).value)}
          />
        </div>
        <div class="col-12">
          <label class="form-label small">
            Secret material keys <span class="text-muted">(comma-separated; stored encrypted)</span>
          </label>
          <input
            class="form-control form-control-sm font-monospace"
            placeholder="client_secret, refresh_token"
            value={secretKeys}
            onInput={(e) => setSecretKeys((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="col-12 text-end">
          <button type="submit" class="btn btn-success btn-sm">
            <i class="bi bi-check2 me-1" />
            Save configuration
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ── Providers list page ──────────────────────────────────────────────

export default function ProvidersPage() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [types, setTypes] = useState<Record<string, ProviderType>>({});
  const [error, setError] = useState("");
  const [isOperator, setIsOperator] = useState(false);
  const [detected, setDetected] = useState<DetectedServer[]>([]);
  const [refreshFor, setRefreshFor] = useState<string | null>(null);

  const load = async () => {
    setError("");
    try {
      setTypes(await loadProviderTypes());
      const resp = await apiFetch<Provider[] | { items?: Provider[] }>(`${API}/providers`);
      const items = Array.isArray(resp) ? resp : (resp.items ?? []);
      setProviders(items);
      return items;
    } catch (e) {
      setError((e as Error).message);
      setProviders([]);
      return [];
    }
  };

  useEffect(() => {
    void (async () => {
      const items = await load();
      await ensureAuth();
      const operator = hasRole("operator");
      setIsOperator(operator);
      if (!operator) return;
      // Best-effort: offer one-click setup for local inference servers.
      try {
        const resp = await apiFetch<{ detected?: DetectedServer[] }>(
          `/api/gateway/local-inference`,
        );
        const knownUrls = new Set(items.map((p) => p.config?.base_url).filter(Boolean));
        setDetected((resp.detected ?? []).filter((d) => !knownUrls.has(d.base_url)));
      } catch {
        // detection is best-effort decoration, never an error state
      }
    })();
  }, []);

  const deleteProvider = async (name: string) => {
    const confirmed = await showConfirm(`Delete provider "${name}"?`, {
      icon: "trash",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!confirmed) return;
    try {
      await apiFetch(`${API}/providers/${name}`, { method: "DELETE" });
      showToast(`Provider "${name}" deleted.`, "success");
      await load();
    } catch (e) {
      showToast(`Delete failed: ${(e as Error).message}`, "danger");
    }
  };

  const basePath = window.location.pathname;

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">Providers</h5>
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-secondary" onClick={() => void load()} title="Refresh">
            <i class="bi bi-arrow-clockwise" />
          </button>
          {isOperator && (
            <a class="btn btn-outline-success" href={`${basePath}/new`}>
              <i class="bi bi-plus-lg me-1" />
              New
            </a>
          )}
        </div>
      </div>

      {detected.length > 0 && (
        <div class="alert alert-info py-2 mb-3">
          <div class="small fw-semibold mb-1">
            <i class="bi bi-lightbulb me-1" />
            Local inference detected on this machine
          </div>
          {detected.map((d) => {
            const models = (d.models ?? []).slice(0, 3).join(", ");
            const params = new URLSearchParams({
              name: d.suggested_name,
              type: d.provider_type,
              base_url: d.base_url,
            });
            return (
              <div key={d.base_url} class="d-flex align-items-center justify-content-between py-1">
                <div>
                  <i class="bi bi-cpu me-2" />
                  <strong>{d.label}</strong>
                  <span class="font-monospace small text-muted ms-2">{d.base_url}</span>
                  {models && (
                    <span class="text-muted small">
                      {" "}
                      serving {models}
                      {(d.models ?? []).length > 3 ? ", …" : ""}
                    </span>
                  )}
                </div>
                <a class="btn btn-sm btn-outline-success ms-3" href={`${basePath}/new?${params}`}>
                  <i class="bi bi-plus-lg me-1" />
                  Create provider
                </a>
              </div>
            );
          })}
        </div>
      )}

      {providers === null && <Spinner message="Loading providers..." />}
      {error && <ErrorAlert message={error} />}
      {providers !== null && !error && providers.length === 0 && (
        <EmptyState icon="key" message="No providers configured.">
          <a class="btn btn-success btn-sm" href={`${basePath}/new`}>
            <i class="bi bi-plus me-1" />
            Create Provider
          </a>
        </EmptyState>
      )}
      {providers !== null && providers.length > 0 && (
        <div class="table-responsive">
          <table class="table table-striped table-hover table-sm align-middle">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th class="d-none d-md-table-cell">Credentials</th>
                <th class="d-none d-md-table-cell">Config</th>
                <th class="text-end">Actions</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((p) => {
                const credKeys = Object.keys(p.credentials ?? {});
                const configEntries = Object.entries(p.config ?? {});
                const expectedKey = types[p.type]?.cred_key || "API_KEY";
                return (
                  <tr key={p.name}>
                    <td>
                      <strong>{p.name}</strong>
                    </td>
                    <td>
                      <i class={`bi bi-${types[p.type]?.icon || "gear"} me-1`} />
                      <span class="badge text-bg-secondary">{p.type}</span>
                    </td>
                    <td class="d-none d-md-table-cell small font-monospace">
                      {credKeys.length > 0 ? (
                        credKeys.map((k) => `${k}=***`).join(", ")
                      ) : (
                        <span class="text-muted">
                          <i class="bi bi-lock-fill me-1" />
                          {expectedKey} (redacted)
                        </span>
                      )}
                    </td>
                    <td class="d-none d-md-table-cell small font-monospace">
                      {configEntries.length > 0 ? (
                        configEntries.map(([k, v]) => `${k}=${v}`).join(", ")
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                    <td class="text-end">
                      {isOperator && (
                        <>
                          <a
                            class="btn btn-sm text-muted"
                            href={`${basePath}/${p.name}/edit`}
                            title="Edit"
                          >
                            <i class="bi bi-pencil" />
                          </a>
                          <button
                            class="btn btn-sm text-muted"
                            title="Credential refresh"
                            onClick={() => setRefreshFor(p.name)}
                          >
                            <i class="bi bi-arrow-repeat" />
                          </button>
                          <button
                            class="btn btn-sm text-muted"
                            title="Delete"
                            onClick={() => void deleteProvider(p.name)}
                          >
                            <i class="bi bi-trash3" />
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {refreshFor && (
        <ProviderRefreshModal provider={refreshFor} onClose={() => setRefreshFor(null)} />
      )}
    </div>
  );
}

// ── Provider create/edit form ────────────────────────────────────────

export function ProviderForm({ mode, providerName }: { mode: string; providerName?: string }) {
  const [types, setTypes] = useState<Record<string, ProviderType>>({});
  const [name, setName] = useState("");
  const [type, setType] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [creds, setCreds] = useState("");
  const [config, setConfig] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [output, setOutput] = useState<{ ok: boolean; text: string } | null>(null);

  const credLabel = types[type]?.cred_key || "API_KEY";
  const apiKeyPlaceholder = mode === "edit" ? "(leave blank to keep current)" : "sk-...";

  useEffect(() => {
    void (async () => {
      const loaded = await loadProviderTypes();
      setTypes(loaded);
      if (mode === "create") {
        const params = new URLSearchParams(window.location.search);
        if (params.get("type")) {
          setName(params.get("name") || "");
          setType(params.get("type")!);
          if (params.get("base_url")) {
            setConfig(`base_url=${params.get("base_url")}`);
            setApiKey("local");
          }
        }
      }
      if (mode === "edit" && providerName) {
        try {
          const resp = await apiFetch<Provider[] | { items?: Provider[] }>(`${API}/providers`);
          const items = Array.isArray(resp) ? resp : (resp.items ?? []);
          const provider = items.find((p) => p.name === providerName);
          if (provider) {
            setName(provider.name);
            setType(provider.type);
            setConfig(
              Object.entries(provider.config ?? {})
                .map(([k, v]) => `${k}=${v}`)
                .join("\n"),
            );
          }
        } catch (e) {
          setOutput({ ok: false, text: (e as Error).message });
        }
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, providerName]);

  const submit = async (e: Event) => {
    e.preventDefault();
    setSubmitting(true);
    setOutput(null);
    try {
      if (mode === "create") {
        if (!name.trim() || !type || !apiKey.trim()) {
          setOutput({ ok: false, text: "Name, type, and API key are required." });
          return;
        }
        const extraCreds = parseKeyValueLines(creds);
        const configObj = parseKeyValueLines(config);
        await apiFetch(`${API}/providers`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: name.trim(),
            type,
            api_key: apiKey.trim(),
            credentials: Object.keys(extraCreds).length > 0 ? extraCreds : undefined,
            config: Object.keys(configObj).length > 0 ? configObj : undefined,
          }),
        });
        showToast(`Provider "${name}" created.`, "success");
        navigateTo(window.location.pathname.replace("/providers/new", "/providers"));
      } else {
        const configObj = parseKeyValueLines(config);
        const extraCreds = parseKeyValueLines(creds);
        const body: Record<string, unknown> = { type, config: configObj };
        if (apiKey.trim() || Object.keys(extraCreds).length > 0) {
          const credentials: Record<string, string> = { ...extraCreds };
          if (apiKey.trim()) credentials[credLabel] = apiKey.trim();
          body.credentials = credentials;
        }
        await apiFetch(`${API}/providers/${providerName}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        showToast(`Provider "${providerName}" updated.`, "success");
        navigateTo(
          window.location.pathname.replace(`/providers/${providerName}/edit`, "/providers"),
        );
      }
    } catch (err) {
      setOutput({ ok: false, text: (err as Error).message });
    } finally {
      setSubmitting(false);
    }
  };

  const cancelHref = window.location.pathname.replace(/\/providers\/.*$/, "/providers");

  return (
    <div class="card sg-card-themed">
      <div class="card-body">
        <form onSubmit={(e) => void submit(e)}>
          <div class="mb-3">
            <label class="form-label">Name</label>
            <input
              type="text"
              class="form-control"
              placeholder="my-provider"
              disabled={mode === "edit"}
              required
              value={name}
              onInput={(e) => setName((e.target as HTMLInputElement).value)}
            />
          </div>
          <div class="mb-3">
            <label class="form-label">Type</label>
            <select
              class="form-select"
              disabled={mode === "edit"}
              required
              value={type}
              onChange={(e) => setType((e.target as HTMLSelectElement).value)}
            >
              <option value="">Select type...</option>
              {Object.values(types).map((t) => (
                <option key={t.type} value={t.type}>
                  {t.label || t.type}
                </option>
              ))}
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">API Key</label>
            <div class="input-group">
              <span class="input-group-text font-monospace small sg-surface-card-wide">
                {credLabel}
              </span>
              <input
                type="password"
                class="form-control font-monospace"
                placeholder={apiKeyPlaceholder}
                autocomplete="off"
                value={apiKey}
                onInput={(e) => setApiKey((e.target as HTMLInputElement).value)}
              />
            </div>
            <div class="form-text">
              {mode === "edit"
                ? "Leave blank to keep the current key."
                : "The environment variable name is set automatically based on the provider type."}
            </div>
          </div>
          <div class="mb-3">
            <label class="form-label">
              Additional Credentials{" "}
              <span class="text-muted small">(optional, KEY=value per line)</span>
            </label>
            <textarea
              class="form-control font-monospace"
              rows={2}
              placeholder="EXTRA_TOKEN=..."
              value={creds}
              onInput={(e) => setCreds((e.target as HTMLTextAreaElement).value)}
            />
          </div>
          <div class="mb-3">
            <label class="form-label">
              Config <span class="text-muted small">(optional, KEY=value per line)</span>
            </label>
            <textarea
              class="form-control font-monospace"
              rows={2}
              placeholder="base_url=https://..."
              value={config}
              onInput={(e) => setConfig((e.target as HTMLTextAreaElement).value)}
            />
          </div>

          {output && (
            <div class={`small ${output.ok ? "text-muted" : "text-danger"}`}>
              {!output.ok && <i class="bi bi-x-circle me-1" />}
              {output.text}
            </div>
          )}

          <div class="d-flex gap-2 mt-3">
            <button type="submit" class="btn btn-success" disabled={submitting}>
              <i class={`bi ${mode === "edit" ? "bi-check" : "bi-plus"} me-1`} />
              {mode === "edit" ? "Save" : "Create"}
            </button>
            <a href={cancelHref} class="btn btn-outline-secondary">
              Cancel
            </a>
          </div>
        </form>
      </div>
    </div>
  );
}
