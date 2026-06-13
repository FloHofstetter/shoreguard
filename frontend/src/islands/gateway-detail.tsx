/** Gateway detail page (island): lifecycle, metadata, inference, settings. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { auth, ensureAuth, hasRole } from "../lib/auth";
import { API, badgeClass, CONFIG, navigateTo } from "../lib/constants";
import { formatTimeAgo } from "../lib/format";
import { showConfirm, showToast } from "../lib/notify";
import { CapabilityNotice, ErrorAlert, isCapabilityError, Spinner } from "../lib/widgets";
import { type GatewayRow, statusIcon, statusLabel } from "./gateways";

interface InferenceProvider {
  name: string;
  label?: string;
  placeholder?: string;
  env_var?: string;
}

interface BundleRoute {
  name: string;
  provider_type?: string;
  model_id?: string;
  base_url?: string;
  protocols?: string[];
  timeout_secs?: number;
  has_api_key?: boolean;
}

interface Bundle {
  revision?: string;
  generated_at_ms?: number;
  routes?: BundleRoute[];
}

// ── Gateway token (diagnostic) ───────────────────────────────────────

function GatewayTokenCard() {
  const [token, setToken] = useState("");
  const [expiresAt, setExpiresAt] = useState(0);
  const [busy, setBusy] = useState(false);

  const call = async (kind: "issue" | "refresh") => {
    setBusy(true);
    try {
      const resp = await apiFetch<{ token?: string; expires_at_ms?: number }>(
        `${API}/tokens/${kind}`,
        { method: "POST" },
      );
      setToken(resp?.token ?? "");
      setExpiresAt(resp?.expires_at_ms ?? 0);
      showToast(`Gateway token ${kind === "issue" ? "issued" : "refreshed"}.`, "success");
    } catch (e) {
      showToast(`Token ${kind} failed: ${(e as Error).message}`, "danger");
    } finally {
      setBusy(false);
    }
  };

  const expiryLabel = expiresAt ? `expires ${new Date(expiresAt).toLocaleString()}` : "non-expiring";

  return (
    <div class="card sg-card-themed mb-4">
      <div class="card-body">
        <h6 class="text-muted mb-2">
          <i class="bi bi-key me-1" />
          Gateway token (diagnostic)
        </h6>
        <p class="small text-muted mb-2">
          Mints a JWT bound to <strong>ShoreGuard's</strong> own gateway identity. Useful to verify
          token issuance against this gateway — it is <em>not</em> a token scoped to a sandbox (the
          upstream RPC binds to the calling identity).
        </p>
        <div class="d-flex gap-2 align-items-center">
          <button
            class="btn btn-outline-secondary btn-sm"
            onClick={() => void call("issue")}
            disabled={busy}
          >
            <i class="bi bi-key me-1" />
            Issue token
          </button>
          <button
            class="btn btn-outline-secondary btn-sm"
            onClick={() => void call("refresh")}
            disabled={busy}
          >
            <i class="bi bi-arrow-repeat me-1" />
            Refresh
          </button>
          {expiresAt > 0 && <span class="small text-muted">{expiryLabel}</span>}
        </div>
        {token && (
          <div class="mt-2">
            <textarea class="form-control form-control-sm font-monospace" rows={2} readOnly>
              {token}
            </textarea>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Resolved inference bundle ────────────────────────────────────────

function InferenceBundle() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [bundle, setBundle] = useState<Bundle>({ revision: "", generated_at_ms: 0, routes: [] });

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setBundle(await apiFetch<Bundle>(`${API}/inference/bundle`));
    } catch (e) {
      setError((e as Error).message || "Failed to load bundle");
      setBundle({ revision: "", generated_at_ms: 0, routes: [] });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const routes = bundle.routes ?? [];

  return (
    <fieldset class="sg-fieldset mb-3">
      <legend class="sg-legend d-flex align-items-center justify-content-between">
        <span>Resolved Inference Bundle</span>
        <button
          class="btn btn-sm btn-outline-secondary"
          onClick={() => void load()}
          disabled={loading}
        >
          <i class="bi bi-arrow-clockwise" />
        </button>
      </legend>
      {loading && (
        <div class="text-muted small">
          <div class="spinner-border spinner-border-sm me-2" />
          Loading bundle…
        </div>
      )}
      {error &&
        (isCapabilityError(error) ? (
          <CapabilityNotice message={error} />
        ) : (
          <div class="text-danger small">{error}</div>
        ))}
      {!loading && !error && (
        <div>
          <div class="small text-muted mb-2">
            Revision <code>{bundle.revision || "—"}</code> · generated{" "}
            <span>
              {bundle.generated_at_ms ? new Date(bundle.generated_at_ms).toLocaleString() : "—"}
            </span>
          </div>
          {routes.length === 0 && <div class="small text-muted">No routes in bundle.</div>}
          {routes.length > 0 && (
            <div class="table-responsive">
              <table class="table table-sm table-borderless align-middle mb-0">
                <thead class="small text-muted">
                  <tr>
                    <th>Route</th>
                    <th>Provider</th>
                    <th>Model</th>
                    <th>Base URL</th>
                    <th>Protocols</th>
                    <th>Timeout</th>
                    <th>API key</th>
                  </tr>
                </thead>
                <tbody>
                  {routes.map((r) => (
                    <tr key={r.name}>
                      <td>
                        <code>{r.name}</code>
                      </td>
                      <td>{r.provider_type || "—"}</td>
                      <td>
                        <code>{r.model_id || "—"}</code>
                      </td>
                      <td class="text-truncate" style="max-width: 18rem" title={r.base_url}>
                        {r.base_url || "—"}
                      </td>
                      <td>{(r.protocols ?? []).join(", ") || "—"}</td>
                      <td>{r.timeout_secs ? `${r.timeout_secs}s` : "—"}</td>
                      <td>
                        {r.has_api_key ? (
                          <span class="badge bg-success-subtle text-success-emphasis">
                            <i class="bi bi-shield-lock-fill me-1" />
                            set
                          </span>
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
      )}
    </fieldset>
  );
}

// ── Observability (OCSF toggle) ──────────────────────────────────────

function ObservabilityFieldset({ name, isAdmin }: { name: string; isAdmin: boolean }) {
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiFetch<{ settings?: Record<string, unknown> }>(`/api/gateway/${name}/settings`)
      .then((config) => setEnabled(config?.settings?.ocsf_json_enabled === true))
      .catch(() => setEnabled(false))
      .finally(() => setLoading(false));
  }, [name]);

  const onToggle = async (next: boolean) => {
    setEnabled(next);
    setSaving(true);
    try {
      await apiFetch(`/api/gateway/${name}/settings/ocsf_json_enabled`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: next }),
      });
      showToast(`OCSF logging ${next ? "enabled" : "disabled"}.`, "success");
    } catch (e) {
      setEnabled(!next);
      showToast(`Failed to update setting: ${(e as Error).message}`, "danger");
    } finally {
      setSaving(false);
    }
  };

  return (
    <fieldset class="sg-fieldset mb-3">
      <legend class="sg-legend">Observability</legend>
      {loading ? (
        <div class="text-muted small">
          <div class="spinner-border spinner-border-sm me-2" />
          Loading settings...
        </div>
      ) : (
        <div>
          <div class="form-check form-switch">
            <input
              class="form-check-input"
              type="checkbox"
              id="gw-ocsf-toggle"
              checked={enabled}
              disabled={!isAdmin || saving}
              onChange={(e) => void onToggle((e.target as HTMLInputElement).checked)}
            />
            <label class="form-check-label" for="gw-ocsf-toggle">
              Enable OCSF logging
            </label>
          </div>
          <div class="small text-muted mt-1">
            When enabled, the gateway streams OCSF security events (network, HTTP, process,
            findings) alongside standard logs.
          </div>
        </div>
      )}
    </fieldset>
  );
}

// ── Advanced settings (raw key-value editor) ─────────────────────────

interface KillSwitchStatus {
  engaged: boolean;
  sandboxes: number;
  engaged_at: string | null;
  engaged_by: string | null;
}

function KillSwitchCard({ name, connected }: { name: string; connected: boolean }) {
  const [status, setStatus] = useState<KillSwitchStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    apiFetch<KillSwitchStatus>(`/api/gateway/${name}/kill-switch`)
      .then(setStatus)
      .catch(() => setStatus(null));
  };
  useEffect(load, [name]);

  const engage = async () => {
    const confirmed = await showConfirm(
      "Cut ALL sandboxes on this gateway off from providers? Agents keep their state " +
        "but instantly lose inference and tool credentials. This is reversible.",
      { btnLabel: "Engage kill switch", btnClass: "btn-danger", icon: "sign-stop" },
    );
    if (!confirmed) return;
    setBusy(true);
    try {
      const report = await apiFetch<{ sandboxes: unknown[]; errors: string[] }>(
        `/api/gateway/${name}/kill-switch`,
        { method: "POST" },
      );
      showToast(
        `Kill switch engaged — ${report.sandboxes.length} sandbox(es) cut` +
          (report.errors.length ? `, ${report.errors.length} error(s)` : ""),
        report.errors.length ? "warning" : "success",
      );
      load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    } finally {
      setBusy(false);
    }
  };

  const release = async () => {
    setBusy(true);
    try {
      const report = await apiFetch<{ sandboxes: unknown[]; errors: string[] }>(
        `/api/gateway/${name}/kill-switch`,
        { method: "DELETE" },
      );
      showToast(
        `Providers re-attached for ${report.sandboxes.length} sandbox(es)` +
          (report.errors.length ? `, ${report.errors.length} error(s) — retry resume` : ""),
        report.errors.length ? "warning" : "success",
      );
      load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    } finally {
      setBusy(false);
    }
  };

  if (!status) return null;
  return (
    <div class={`card sg-card-themed mb-4 ${status.engaged ? "border-danger" : ""}`}>
      <div class="card-body d-flex align-items-center justify-content-between flex-wrap gap-2">
        <div>
          <div class="fw-semibold">
            <i class={`bi bi-sign-stop me-2 ${status.engaged ? "text-danger" : ""}`} />
            Kill switch
            {status.engaged && (
              <span class="badge text-bg-danger ms-2">
                ENGAGED — {status.sandboxes} sandbox(es) cut
              </span>
            )}
          </div>
          <div class="text-muted small">
            {status.engaged
              ? `Engaged ${status.engaged_at ? new Date(status.engaged_at).toLocaleString() : ""} by ${status.engaged_by ?? "?"} — agents have no provider access.`
              : "Reversibly detach every sandbox's providers — agents instantly lose inference and tool credentials, state is preserved."}
          </div>
        </div>
        {status.engaged ? (
          <button class="btn btn-outline-success" disabled={busy} onClick={() => void release()}>
            {busy && <span class="spinner-border spinner-border-sm me-2" />}
            <i class="bi bi-play-circle me-1" />
            Resume providers
          </button>
        ) : (
          <button
            class="btn btn-outline-danger"
            disabled={busy || !connected}
            onClick={() => void engage()}
          >
            {busy && <span class="spinner-border spinner-border-sm me-2" />}
            <i class="bi bi-sign-stop me-1" />
            Cut all providers
          </button>
        )}
      </div>
    </div>
  );
}

interface Curfew {
  configured?: boolean;
  enabled?: boolean;
  start_minute?: number;
  end_minute?: number;
  timezone?: string;
}

function minuteToTime(minute: number): string {
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}

function timeToMinute(value: string): number {
  const [h, m] = value.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

function CurfewCard({ name }: { name: string }) {
  const [curfew, setCurfew] = useState<Curfew | null>(null);
  const [start, setStart] = useState("22:00");
  const [end, setEnd] = useState("07:00");
  const [tz, setTz] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = () => {
    apiFetch<Curfew>(`/api/gateway/${name}/curfew`)
      .then((c) => {
        setCurfew(c);
        if (c.start_minute !== undefined) {
          setStart(minuteToTime(c.start_minute));
          setEnd(minuteToTime(c.end_minute ?? 0));
          setTz(c.timezone || "UTC");
          setEnabled(c.enabled ?? true);
        }
      })
      .catch(() => setCurfew(null));
  };
  useEffect(load, [name]);

  const save = async () => {
    setBusy(true);
    try {
      await apiFetch(`/api/gateway/${name}/curfew`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled,
          start_minute: timeToMinute(start),
          end_minute: timeToMinute(end),
          timezone: tz,
        }),
      });
      showToast("Curfew saved.", "success");
      load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    try {
      await apiFetch(`/api/gateway/${name}/curfew`, { method: "DELETE" });
      showToast("Curfew removed.", "success");
      load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  if (curfew === null) return null;
  const configured = curfew.start_minute !== undefined;
  return (
    <div class="card sg-card-themed mb-4">
      <div class="card-body">
        <div class="fw-semibold mb-1">
          <i class="bi bi-moon-stars me-2" />
          Curfew (quiet hours)
          {configured && curfew.enabled && (
            <span class="badge text-bg-info ms-2">
              {minuteToTime(curfew.start_minute!)}–{minuteToTime(curfew.end_minute!)}{" "}
              {curfew.timezone}
            </span>
          )}
        </div>
        <div class="text-muted small mb-2">
          Inside the window the kill switch engages automatically (reversible — providers
          re-attach when the window ends). Manually engaged switches are never touched.
        </div>
        <div class="d-flex flex-wrap align-items-end gap-2">
          <div>
            <label class="form-label small text-muted mb-1">From</label>
            <input
              type="time"
              class="form-control form-control-sm"
              value={start}
              onInput={(e) => setStart((e.target as HTMLInputElement).value)}
            />
          </div>
          <div>
            <label class="form-label small text-muted mb-1">Until</label>
            <input
              type="time"
              class="form-control form-control-sm"
              value={end}
              onInput={(e) => setEnd((e.target as HTMLInputElement).value)}
            />
          </div>
          <div>
            <label class="form-label small text-muted mb-1">Timezone</label>
            <input
              type="text"
              class="form-control form-control-sm"
              value={tz}
              onInput={(e) => setTz((e.target as HTMLInputElement).value)}
            />
          </div>
          <div class="form-check form-switch mb-1 ms-1">
            <input
              class="form-check-input"
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled((e.target as HTMLInputElement).checked)}
            />
            <label class="form-check-label small">Enabled</label>
          </div>
          <button class="btn btn-sm btn-outline-primary" disabled={busy} onClick={() => void save()}>
            {configured ? "Update" : "Set curfew"}
          </button>
          {configured && (
            <button class="btn btn-sm btn-outline-danger" onClick={() => void remove()}>
              Remove
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function AdvancedSettings({ name, isAdmin }: { name: string; isAdmin: boolean }) {
  const [rows, setRows] = useState<Record<string, { current: string; draft: string; busy: boolean }>>(
    {},
  );
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    apiFetch<{ settings?: Record<string, unknown> }>(`/api/gateway/${name}/settings`)
      .then((resp) => {
        const settings = resp?.settings ?? {};
        const next: typeof rows = {};
        for (const [k, v] of Object.entries(settings)) {
          const s = typeof v === "string" ? v : JSON.stringify(v);
          next[k] = { current: s, draft: s, busy: false };
        }
        setRows(next);
      })
      .catch((e: Error) => setStatus({ ok: false, text: `Load failed: ${e.message}` }))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const valueForWire = (raw: string): unknown => {
    const trimmed = raw.trim();
    try {
      return JSON.parse(trimmed);
    } catch {
      return raw;
    }
  };

  const setBusy = (key: string, busy: boolean) =>
    setRows((prev) => ({ ...prev, [key]: { ...prev[key], busy } }));

  const saveKey = async (key: string) => {
    const row = rows[key];
    if (!row) return;
    setBusy(key, true);
    setStatus(null);
    try {
      await apiFetch(`/api/gateway/${name}/settings/${encodeURIComponent(key)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: valueForWire(row.draft) }),
      });
      setRows((prev) => ({
        ...prev,
        [key]: { ...prev[key], current: prev[key].draft, busy: false },
      }));
      setStatus({ ok: true, text: `Saved ${key}.` });
      showToast(`Setting ${key} saved.`, "success");
    } catch (e) {
      setBusy(key, false);
      setStatus({ ok: false, text: `Save failed for ${key}: ${(e as Error).message}` });
      showToast(`Gateway rejected ${key}: ${(e as Error).message}`, "danger");
    }
  };

  const deleteKey = async (key: string) => {
    const confirmed = await showConfirm(`Delete gateway setting "${key}"?`, {
      icon: "trash",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!confirmed) return;
    setBusy(key, true);
    setStatus(null);
    try {
      await apiFetch(`/api/gateway/${name}/settings/${encodeURIComponent(key)}`, {
        method: "DELETE",
      });
      setRows((prev) => {
        const { [key]: _removed, ...rest } = prev;
        return rest;
      });
      setStatus({ ok: true, text: `Deleted ${key}.` });
      showToast(`Setting ${key} deleted.`, "success");
    } catch (e) {
      setBusy(key, false);
      setStatus({ ok: false, text: `Delete failed: ${(e as Error).message}` });
      showToast(`Delete failed: ${(e as Error).message}`, "danger");
    }
  };

  const addKey = async () => {
    const key = newKey.trim();
    if (!key) return;
    if (rows[key]) {
      showToast(`Setting ${key} already exists — edit the row instead.`, "warning");
      return;
    }
    setAdding(true);
    setStatus(null);
    try {
      await apiFetch(`/api/gateway/${name}/settings/${encodeURIComponent(key)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: valueForWire(newValue) }),
      });
      setRows((prev) => ({ ...prev, [key]: { current: newValue, draft: newValue, busy: false } }));
      setNewKey("");
      setNewValue("");
      setStatus({ ok: true, text: `Added ${key}.` });
      showToast(`Setting ${key} added.`, "success");
    } catch (e) {
      setStatus({ ok: false, text: `Add failed: ${(e as Error).message}` });
      showToast(`Gateway rejected ${key}: ${(e as Error).message}`, "danger");
    } finally {
      setAdding(false);
    }
  };

  return (
    <fieldset class="sg-fieldset mb-3 mt-3">
      <legend class="sg-legend d-flex align-items-center">
        <button
          class="btn btn-sm btn-link text-decoration-none p-0 me-2"
          onClick={() => setExpanded(!expanded)}
        >
          <i class={`bi ${expanded ? "bi-chevron-down" : "bi-chevron-right"}`} />
        </button>
        Advanced Settings
        {!loading && <span class="badge text-bg-secondary ms-2">{Object.keys(rows).length}</span>}
      </legend>
      {expanded && (
        <div>
          <div class="alert alert-warning small mb-3">
            <i class="bi bi-exclamation-triangle-fill me-1" />
            Changing settings bypasses validation ShoreGuard cannot perform. The gateway will
            reject unknown keys or invalid values — the error text below will tell you which.
          </div>
          {loading ? (
            <div class="text-muted small">
              <div class="spinner-border spinner-border-sm me-2" />
              Loading settings…
            </div>
          ) : (
            <div>
              <div class="table-responsive">
                <table class="table table-sm align-middle mb-0">
                  <thead>
                    <tr>
                      <th style="width:30%">Key</th>
                      <th>Value</th>
                      <th style="width:1%" />
                    </tr>
                  </thead>
                  <tbody>
                    {Object.keys(rows)
                      .sort()
                      .map((key) => (
                        <tr key={key}>
                          <td class="font-monospace small">{key}</td>
                          <td>
                            <input
                              class="form-control form-control-sm font-monospace"
                              type="text"
                              disabled={!isAdmin || rows[key].busy}
                              value={rows[key].draft}
                              onInput={(e) =>
                                setRows((prev) => ({
                                  ...prev,
                                  [key]: {
                                    ...prev[key],
                                    draft: (e.target as HTMLInputElement).value,
                                  },
                                }))
                              }
                            />
                          </td>
                          <td class="text-nowrap">
                            <button
                              class="btn btn-sm btn-outline-primary me-1"
                              disabled={
                                !isAdmin || rows[key].busy || rows[key].draft === rows[key].current
                              }
                              onClick={() => void saveKey(key)}
                            >
                              <i class="bi bi-check-lg" />
                            </button>
                            <button
                              class="btn btn-sm btn-outline-danger"
                              disabled={!isAdmin || rows[key].busy}
                              onClick={() => void deleteKey(key)}
                            >
                              <i class="bi bi-trash" />
                            </button>
                          </td>
                        </tr>
                      ))}
                    <tr>
                      <td>
                        <input
                          class="form-control form-control-sm font-monospace"
                          type="text"
                          placeholder="new.setting.key"
                          disabled={!isAdmin || adding}
                          value={newKey}
                          onInput={(e) => setNewKey((e.target as HTMLInputElement).value)}
                        />
                      </td>
                      <td>
                        <input
                          class="form-control form-control-sm font-monospace"
                          type="text"
                          placeholder="value"
                          disabled={!isAdmin || adding}
                          value={newValue}
                          onInput={(e) => setNewValue((e.target as HTMLInputElement).value)}
                        />
                      </td>
                      <td class="text-nowrap">
                        <button
                          class="btn btn-sm btn-outline-success"
                          disabled={!isAdmin || adding || !newKey.trim()}
                          onClick={() => void addKey()}
                        >
                          <i class="bi bi-plus-lg" /> Add
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              {status && (
                <div class={`small mt-2 ${status.ok ? "text-success" : "text-danger"}`}>
                  {status.text}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </fieldset>
  );
}

// ── Main detail page ─────────────────────────────────────────────────

export default function GatewayDetailPage({ name }: { name: string }) {
  const [gw, setGw] = useState<GatewayRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionOutput, setActionOutput] = useState<{ text: string; cls: string } | null>(null);
  const [acting, setActing] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);

  const [metaDescription, setMetaDescription] = useState("");
  const [metaLabels, setMetaLabels] = useState<{ key: string; val: string }[]>([]);
  const [newMetaKey, setNewMetaKey] = useState("");
  const [newMetaVal, setNewMetaVal] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveOutput, setSaveOutput] = useState<{ ok: boolean; text: string } | null>(null);

  // Inference config state (saved together with metadata via Save).
  const [infProviders, setInfProviders] = useState<InferenceProvider[]>([]);
  const [infLoading, setInfLoading] = useState(false);
  const [infProvider, setInfProvider] = useState("");
  const [infModelId, setInfModelId] = useState("");
  const [infTimeout, setInfTimeout] = useState(0);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await apiFetch<GatewayRow[] | { items?: GatewayRow[] }>(`/api/gateway/list`);
      const gateways = Array.isArray(resp) ? resp : (resp.items ?? []);
      const found = gateways.find((g) => g.name === name) ?? null;
      setGw(found);
      if (!found) return;
      setMetaDescription(found.description ?? "");
      setMetaLabels(Object.entries(found.labels ?? {}).map(([key, val]) => ({ key, val })));
      if (found.connected) {
        setInfLoading(true);
        try {
          const [providers, config] = await Promise.all([
            apiFetch<InferenceProvider[]>(`${API}/providers/inference-providers`).catch(
              () => [] as InferenceProvider[],
            ),
            apiFetch<{ provider_name?: string; model_id?: string; timeout_secs?: number }>(
              `${API}/inference`,
            ).catch(() => null),
          ]);
          setInfProviders(providers);
          setInfProvider(config?.provider_name ?? "");
          setInfModelId(config?.model_id ?? "");
          setInfTimeout(config?.timeout_secs ?? 0);
        } finally {
          setInfLoading(false);
        }
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    void ensureAuth().then(() => setIsAdmin(hasRole("admin")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const lifecycleAction = async (action: "start" | "stop" | "restart") => {
    setActing(true);
    setActionOutput({ text: `${action[0].toUpperCase()}${action.slice(1)}ing gateway...`, cls: "" });
    try {
      type LifecycleResult = { success?: boolean; output?: string; error?: string };
      const result =
        action === "start"
          ? await apiFetch<LifecycleResult>(`/api/gateway/${name}/start`, { method: "POST" })
          : action === "stop"
            ? await apiFetch<LifecycleResult>(`/api/gateway/${name}/stop`, { method: "POST" })
            : await apiFetch<LifecycleResult>(`/api/gateway/${name}/restart`, { method: "POST" });
      if (result.success) {
        setActionOutput({ text: `Gateway ${action}ed. ${result.output || ""}`, cls: "log-info" });
        showToast(`Gateway ${action}ed.`, "success");
        setTimeout(() => void load(), CONFIG.actionRefreshDelay);
      } else {
        setActionOutput({
          text: `${action} failed: ${result.error || "Unknown error"}`,
          cls: "log-error",
        });
      }
    } catch (e) {
      setActionOutput({ text: `Error: ${(e as Error).message}`, cls: "log-error" });
    } finally {
      setActing(false);
    }
  };

  const testConnection = async () => {
    setActionOutput({ text: "Testing connection...", cls: "" });
    try {
      const result = await apiFetch<{
        success?: boolean;
        version?: string;
        health_status?: string;
        error?: string;
      }>(`/api/gateway/${name}/test-connection`, { method: "POST" });
      if (result.success) {
        setActionOutput({
          text: `Connected! ${result.version ? `v${result.version}` : ""} (${result.health_status || "ok"})`,
          cls: "log-info",
        });
        showToast("Connection successful.", "success");
        setTimeout(() => void load(), CONFIG.actionRefreshDelay);
      } else {
        setActionOutput({
          text: `Connection failed: ${result.error || "Unknown error"}`,
          cls: "log-error",
        });
        showToast("Connection failed.", "danger");
      }
    } catch (e) {
      setActionOutput({ text: `Error: ${(e as Error).message}`, cls: "log-error" });
    }
  };

  const unregister = async () => {
    const confirmed = await showConfirm(
      `Unregister gateway "${name}"? This removes it from Shoreguard but does not affect the running gateway.`,
      { icon: "trash", iconColor: "text-danger", btnClass: "btn-danger", btnLabel: "Unregister" },
    );
    if (!confirmed) return;
    try {
      const result = await apiFetch<{ success?: boolean; error?: string }>(`/api/gateway/${name}`, {
        method: "DELETE",
      });
      if (result.success) {
        showToast(`Gateway "${name}" unregistered.`, "success");
        navigateTo("/gateways");
      } else {
        showToast(`Failed: ${result.error}`, "danger");
      }
    } catch (e) {
      showToast(`Error: ${(e as Error).message}`, "danger");
    }
  };

  const addMetaLabel = () => {
    const key = newMetaKey.trim();
    const val = newMetaVal.trim();
    if (!key) return;
    if (metaLabels.some((r) => r.key === key)) return;
    if (metaLabels.length >= 20) return;
    setMetaLabels([...metaLabels, { key, val }]);
    setNewMetaKey("");
    setNewMetaVal("");
  };

  const saveAll = async () => {
    setSaving(true);
    setSaveOutput(null);
    const errors: string[] = [];

    const metaBody: Record<string, unknown> = { description: metaDescription.trim() || null };
    metaBody.labels =
      metaLabels.length > 0 ? Object.fromEntries(metaLabels.map((r) => [r.key, r.val])) : null;
    try {
      await apiFetch(`/api/gateway/${name}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(metaBody),
      });
    } catch (e) {
      errors.push(`Metadata: ${(e as Error).message}`);
    }

    if (gw?.connected && infProvider) {
      try {
        await apiFetch(`${API}/inference`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider_name: infProvider,
            model_id: infModelId,
            verify: false,
            timeout_secs: infTimeout,
          }),
        });
      } catch (e) {
        errors.push(`Inference: ${(e as Error).message}`);
      }
    }

    if (errors.length === 0) {
      setSaveOutput({ ok: true, text: "Saved" });
      setTimeout(() => setSaveOutput(null), 2000);
      void load();
    } else {
      setSaveOutput({ ok: false, text: errors.join("; ") });
    }
    setSaving(false);
  };

  if (loading) return <Spinner message="Loading gateway..." />;
  if (error) return <ErrorAlert message={error} />;
  if (!gw) return <div class="alert alert-warning">Gateway "{name}" not found.</div>;

  const connected = gw.status === "connected";
  const localMode = auth.value.localMode;
  const currentInfProvider = infProviders.find((p) => p.name === infProvider) ?? null;
  const dateSuffix = (iso: string) =>
    `(${new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })})`;

  return (
    <div>
      <div class="d-flex align-items-center justify-content-between mb-4 pb-3 border-bottom sg-border-sg">
        <div class="d-flex align-items-center gap-3">
          <span class={`badge ${badgeClass("gateway", gw.status ?? "offline")}`}>
            <i class={`bi me-1 bi-${statusIcon(gw.status)}`} />
            <span>{statusLabel(gw.status)}</span>
          </span>
          {gw.version && <span class="text-muted">v{gw.version}</span>}
        </div>
        <div class="d-flex gap-2">
          {localMode && isAdmin && !connected && (
            <button
              class="btn btn-outline-success btn-sm"
              disabled={acting}
              onClick={() => void lifecycleAction("start")}
            >
              {acting ? (
                <span class="spinner-border spinner-border-sm me-1" />
              ) : (
                <i class="bi bi-play-fill me-1" />
              )}
              Start
            </button>
          )}
          {localMode && isAdmin && connected && (
            <button
              class="btn btn-outline-danger btn-sm"
              disabled={acting}
              onClick={() => void lifecycleAction("stop")}
            >
              {acting ? (
                <span class="spinner-border spinner-border-sm me-1" />
              ) : (
                <i class="bi bi-stop-fill me-1" />
              )}
              Stop
            </button>
          )}
          {localMode && isAdmin && connected && (
            <button
              class="btn btn-outline-warning btn-sm"
              disabled={acting}
              onClick={() => void lifecycleAction("restart")}
            >
              {acting ? (
                <span class="spinner-border spinner-border-sm me-1" />
              ) : (
                <i class="bi bi-arrow-repeat me-1" />
              )}
              Restart
            </button>
          )}
          {isAdmin && (
            <button class="btn btn-outline-primary btn-sm" onClick={() => void testConnection()}>
              <i class="bi bi-plug me-1" />
              Test Connection
            </button>
          )}
          {isAdmin && (
            <button
              class="btn btn-outline-danger btn-sm"
              title="Unregister"
              onClick={() => void unregister()}
            >
              <i class="bi bi-trash" />
            </button>
          )}
          <button class="btn btn-outline-secondary btn-sm" title="Refresh" onClick={() => void load()}>
            <i class="bi bi-arrow-clockwise" />
          </button>
        </div>
      </div>

      {actionOutput && (
        <div class="mb-3">
          <div class="log-output small">
            <div class={`log-line ${actionOutput.cls}`}>{actionOutput.text}</div>
          </div>
        </div>
      )}

      <div class="row g-3 mb-4">
        {(
          [
            ["/sandboxes", "bi-grid", "Sandboxes", "btn-outline-secondary"],
            ["/providers", "bi-key", "Providers", "btn-outline-secondary"],
            ["/provider-profiles", "bi-collection", "Profiles", "btn-outline-secondary"],
            ["/services", "bi-hdd-network", "Services", "btn-outline-secondary"],
            ["/wizard", "bi-plus-circle", "New Sandbox", "btn-outline-success"],
          ] as const
        ).map(([path, icon, label, btn]) => (
          <div key={path} class="col">
            <a
              href={connected ? `/gateways/${name}${path}` : undefined}
              class={`btn ${btn} w-100 py-3 ${connected ? "" : "disabled"}`}
            >
              <i class={`bi ${icon} me-2`} />
              {label}
            </a>
          </div>
        ))}
      </div>

      {isAdmin && <KillSwitchCard name={name} connected={connected} />}
      {isAdmin && <CurfewCard name={name} />}

      {isAdmin && <GatewayTokenCard />}

      <div class="card sg-card-themed mb-4">
        <div class="card-body">
          <fieldset class="sg-fieldset mb-3">
            <legend class="sg-legend">Metadata</legend>
            <div class="row g-3">
              <div class="col-12">
                <label class="form-label small text-muted">
                  <i class="bi bi-card-text me-1" />
                  Description
                </label>
                {isAdmin ? (
                  <input
                    type="text"
                    class="form-control form-control-sm"
                    placeholder="e.g. Production EU-West for ML team"
                    maxLength={1000}
                    value={metaDescription}
                    onInput={(e) => setMetaDescription((e.target as HTMLInputElement).value)}
                  />
                ) : (
                  <div class="form-control-plaintext small">{gw.description || "—"}</div>
                )}
              </div>
              <div class="col-12">
                <label class="form-label small text-muted">
                  <i class="bi bi-tags me-1" />
                  Labels
                </label>
                {metaLabels.length > 0 && (
                  <div class="mb-2 d-flex flex-wrap gap-1">
                    {metaLabels.map((lbl) => (
                      <span
                        key={lbl.key}
                        class="badge text-bg-light border d-inline-flex align-items-center gap-1"
                      >
                        <span class="font-monospace">{lbl.key}</span>
                        <span class="text-muted">=</span>
                        <span>{lbl.val}</span>
                        {isAdmin && (
                          <button
                            type="button"
                            class="btn-close btn-close-sm ms-1 sg-fs-xxs"
                            onClick={() =>
                              setMetaLabels(metaLabels.filter((r) => r.key !== lbl.key))
                            }
                          />
                        )}
                      </span>
                    ))}
                  </div>
                )}
                {isAdmin && metaLabels.length < 20 && (
                  <div class="input-group input-group-sm sg-mw-400">
                    <input
                      type="text"
                      class="form-control font-monospace"
                      placeholder="key"
                      value={newMetaKey}
                      onInput={(e) => setNewMetaKey((e.target as HTMLInputElement).value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addMetaLabel();
                        }
                      }}
                    />
                    <span class="input-group-text">=</span>
                    <input
                      type="text"
                      class="form-control"
                      placeholder="value"
                      value={newMetaVal}
                      onInput={(e) => setNewMetaVal((e.target as HTMLInputElement).value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addMetaLabel();
                        }
                      }}
                    />
                    <button
                      class="btn btn-outline-success"
                      type="button"
                      onClick={addMetaLabel}
                      disabled={!newMetaKey.trim()}
                    >
                      <i class="bi bi-plus" />
                    </button>
                  </div>
                )}
                {!isAdmin && metaLabels.length === 0 && (
                  <span class="text-muted fst-italic small">No labels</span>
                )}
              </div>
            </div>
          </fieldset>

          <fieldset class="sg-fieldset mb-3">
            <legend class="sg-legend">Connection</legend>
            <table class="table table-sm table-borderless mb-0 align-middle">
              <tbody>
                <tr>
                  <td class="text-muted sg-w-120">
                    <i class="bi bi-globe me-2" />
                    URL
                  </td>
                  <td>
                    <span class="endpoint-badge px-2 py-1">
                      {(gw.scheme as string) || "https"}://{gw.endpoint || "—"}
                    </span>
                  </td>
                </tr>
                {gw.auth_mode && (
                  <tr>
                    <td class="text-muted">
                      <i class="bi bi-shield-check me-2" />
                      Auth
                    </td>
                    <td>{gw.auth_mode}</td>
                  </tr>
                )}
                {gw.registered_at != null && (
                  <tr>
                    <td class="text-muted">
                      <i class="bi bi-calendar-plus me-2" />
                      Registered
                    </td>
                    <td>
                      <span>{formatTimeAgo(gw.registered_at as string)}</span>
                      <span class="text-muted small ms-1">
                        {dateSuffix(gw.registered_at as string)}
                      </span>
                    </td>
                  </tr>
                )}
                {gw.last_seen && (
                  <tr>
                    <td class="text-muted">
                      <i class="bi bi-clock-history me-2" />
                      Last Seen
                    </td>
                    <td>
                      <span>{formatTimeAgo(gw.last_seen)}</span>
                      <span class="text-muted small ms-1">{dateSuffix(gw.last_seen)}</span>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </fieldset>

          {connected && (
            <fieldset class="sg-fieldset mb-3">
              <legend class="sg-legend">Inference</legend>
              {infLoading ? (
                <div class="text-muted small">
                  <div class="spinner-border spinner-border-sm me-2" />
                  Loading provider config...
                </div>
              ) : (
                <div>
                  <div class="row g-3 align-items-end">
                    <div class="col-md-4">
                      <label class="form-label small text-muted">Provider</label>
                      <select
                        class="form-select form-select-sm"
                        value={infProvider}
                        onChange={(e) => {
                          const next = (e.target as HTMLSelectElement).value;
                          setInfProvider(next);
                          const cp = infProviders.find((p) => p.name === next);
                          if (cp && !infModelId) setInfModelId(cp.placeholder ?? "");
                        }}
                      >
                        <option value="">— Select —</option>
                        {infProviders.map((p) => (
                          <option key={p.name} value={p.name}>
                            {p.label || p.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div class="col-md-4">
                      <label class="form-label small text-muted">Model ID</label>
                      <input
                        type="text"
                        class="form-control form-control-sm"
                        placeholder={currentInfProvider?.placeholder || "model-id"}
                        value={infModelId}
                        onInput={(e) => setInfModelId((e.target as HTMLInputElement).value)}
                      />
                    </div>
                    <div class="col-md-4">
                      <label class="form-label small text-muted">Timeout (s)</label>
                      <input
                        type="number"
                        class="form-control form-control-sm"
                        placeholder="0"
                        min={0}
                        value={infTimeout}
                        onInput={(e) =>
                          setInfTimeout(parseInt((e.target as HTMLInputElement).value, 10) || 0)
                        }
                      />
                    </div>
                  </div>
                  {infProvider ? (
                    <div class="mt-2 small text-muted">
                      <i class="bi bi-info-circle me-1" />
                      API key must be set as environment variable{" "}
                      {currentInfProvider?.env_var && <code>{currentInfProvider.env_var}</code>} on
                      the gateway host.
                    </div>
                  ) : (
                    <div class="mt-2 small text-warning">
                      <i class="bi bi-exclamation-triangle me-1" />
                      No inference provider configured. Sandboxes need a provider to run agents.
                    </div>
                  )}
                </div>
              )}
            </fieldset>
          )}

          {connected && <InferenceBundle />}
          {connected && <ObservabilityFieldset name={name} isAdmin={isAdmin} />}

          {isAdmin && (
            <div class="d-flex align-items-center gap-2 pt-2">
              <button class="btn btn-primary btn-sm" onClick={() => void saveAll()} disabled={saving}>
                {saving ? (
                  <span class="spinner-border spinner-border-sm me-1" />
                ) : (
                  <i class="bi bi-check-lg me-1" />
                )}
                Save
              </button>
              {saveOutput && (
                <span class={`small ${saveOutput.ok ? "text-success" : "text-danger"}`}>
                  {saveOutput.ok && <i class="bi bi-check-circle me-1" />}
                  {saveOutput.text}
                </span>
              )}
            </div>
          )}

          {connected && <AdvancedSettings name={name} isAdmin={isAdmin} />}
        </div>
      </div>
    </div>
  );
}
