/** Sandbox detail page (island): hero, summary cards, metadata, providers. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth, hasRole } from "../lib/auth";
import { API, badgeClass, gwUrl } from "../lib/constants";
import { showConfirm, showToast } from "../lib/notify";
import { connectSandboxWebSocket } from "../lib/sandbox-ws";
import { ErrorAlert, Spinner } from "../lib/widgets";
import type { Sandbox } from "./sandboxes";

interface Provider {
  name: string;
  type?: string;
}

interface DraftChunk {
  status: string;
  security_notes?: string;
}

interface Draft {
  chunks?: DraftChunk[];
  last_analyzed_at_ms?: number;
}

function formatTimestamp(ms: number | undefined): string {
  return ms ? new Date(ms).toLocaleString() : "";
}

export default function SandboxDetailPage({ name }: { name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sandbox, setSandbox] = useState<Sandbox | null>(null);
  const [pendingCount, setPendingCount] = useState(0);
  const [securityFlaggedCount, setSecurityFlaggedCount] = useState(0);
  const [lastAnalyzedAtMs, setLastAnalyzedAtMs] = useState(0);
  const [networkCount, setNetworkCount] = useState(0);
  const [wsState, setWsState] = useState("connecting");
  const [isOperator, setIsOperator] = useState(false);

  const [metaDescription, setMetaDescription] = useState("");
  const [metaLabels, setMetaLabels] = useState<{ key: string; val: string }[]>([]);
  const [newMetaKey, setNewMetaKey] = useState("");
  const [newMetaVal, setNewMetaVal] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveOutput, setSaveOutput] = useState<{ ok: boolean; text: string } | null>(null);

  const [attachedProviders, setAttachedProviders] = useState<Provider[]>([]);
  const [availableProviders, setAvailableProviders] = useState<Provider[]>([]);
  const [attachProviderName, setAttachProviderName] = useState("");
  const [attachBusy, setAttachBusy] = useState(false);
  const [attachError, setAttachError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [sb, draft, policyData, attached] = await Promise.all([
        apiFetch<Sandbox>(`${API}/sandboxes/${name}`),
        apiFetch<Draft>(`${API}/sandboxes/${name}/approvals`).catch(() => null),
        apiFetch<{ policy?: { network_policies?: Record<string, unknown> } }>(
          `${API}/sandboxes/${name}/policy`,
        ).catch(() => null),
        apiFetch<Provider[]>(`${API}/sandboxes/${name}/providers`).catch(() => []),
      ]);
      setSandbox(sb);
      setMetaDescription(sb.description ?? "");
      setMetaLabels(Object.entries(sb.labels ?? {}).map(([key, val]) => ({ key, val })));
      setAttachedProviders(Array.isArray(attached) ? attached : []);
      const chunks = draft?.chunks ?? [];
      const pending = chunks.filter((c) => c.status === "pending");
      setPendingCount(pending.length);
      setSecurityFlaggedCount(pending.filter((c) => c.security_notes?.trim()).length);
      setLastAnalyzedAtMs(draft?.last_analyzed_at_ms ?? 0);
      const policy = policyData?.policy ?? null;
      setNetworkCount(policy ? Object.keys(policy.network_policies ?? {}).length : 0);
      connectSandboxWebSocket(sb.name, sb.id);
    } catch {
      setError(`Sandbox "${name}" not found.`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    void ensureAuth().then(() => setIsOperator(hasRole("operator")));
    const onWsState = (ev: Event) => {
      const detail = (ev as CustomEvent).detail;
      if (detail?.sandboxName === name) setWsState(detail.state);
    };
    const onStatus = (ev: Event) => {
      const detail = (ev as CustomEvent).detail;
      if (detail?.sandboxName === name && detail.phase) {
        setSandbox((sb) => (sb ? { ...sb, phase: detail.phase } : sb));
      }
    };
    document.addEventListener("sg:ws-state", onWsState);
    document.addEventListener("sg:sandbox-status", onStatus);
    return () => {
      document.removeEventListener("sg:ws-state", onWsState);
      document.removeEventListener("sg:sandbox-status", onStatus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

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

  const saveMeta = async () => {
    setSaving(true);
    setSaveOutput(null);
    const body: Record<string, unknown> = { description: metaDescription.trim() || null };
    body.labels =
      metaLabels.length > 0
        ? Object.fromEntries(metaLabels.map((r) => [r.key, r.val]))
        : null;
    try {
      await apiFetch(`${API}/sandboxes/${name}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setSaveOutput({ ok: true, text: "Saved" });
      setTimeout(() => setSaveOutput(null), 2000);
      void load();
    } catch (e) {
      setSaveOutput({ ok: false, text: (e as Error).message });
    }
    setSaving(false);
  };

  const loadAvailableProviders = async () => {
    if (availableProviders.length > 0) return;
    try {
      const resp = await apiFetch<Provider[] | { items?: Provider[] }>(`${API}/providers`);
      setAvailableProviders(Array.isArray(resp) ? resp : (resp.items ?? []));
    } catch (e) {
      setAttachError(`Failed to load providers: ${(e as Error).message}`);
    }
  };

  const refreshAttachedProviders = async () => {
    try {
      const resp = await apiFetch<Provider[]>(`${API}/sandboxes/${name}/providers`);
      setAttachedProviders(Array.isArray(resp) ? resp : []);
    } catch {
      setAttachedProviders([]);
    }
  };

  const attachProvider = async () => {
    const provName = attachProviderName.trim();
    if (!provName) return;
    setAttachBusy(true);
    setAttachError("");
    try {
      const resp = await apiFetch<{ attached?: boolean }>(`${API}/sandboxes/${name}/providers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider_name: provName }),
      });
      showToast(
        resp.attached
          ? `Provider "${provName}" attached.`
          : `Provider "${provName}" was already attached.`,
        resp.attached ? "success" : "info",
      );
      setAttachProviderName("");
      await refreshAttachedProviders();
    } catch (e) {
      setAttachError((e as Error).message);
    } finally {
      setAttachBusy(false);
    }
  };

  const detachProvider = async (providerName: string) => {
    const confirmed = await showConfirm(`Detach provider "${providerName}" from this sandbox?`, {
      icon: "plug",
      btnLabel: "Detach",
    });
    if (!confirmed) return;
    try {
      const resp = await apiFetch<{ detached?: boolean }>(
        `${API}/sandboxes/${name}/providers/${providerName}`,
        { method: "DELETE" },
      );
      showToast(
        resp.detached
          ? `Provider "${providerName}" detached.`
          : `Provider "${providerName}" was not attached.`,
        resp.detached ? "success" : "info",
      );
      await refreshAttachedProviders();
    } catch (e) {
      showToast(`Detach failed: ${(e as Error).message}`, "danger");
    }
  };

  if (loading) return <Spinner message="Loading sandbox..." />;
  if (error || !sandbox) return <ErrorAlert message={error || "Not found"} />;

  const attachedNames = new Set(attachedProviders.map((p) => p.name));
  const attachableProviders = availableProviders.filter((p) => !attachedNames.has(p.name));
  const networkLabel = networkCount === 1 ? "1 network rule" : `${networkCount} network rules`;
  const pendingLabel =
    pendingCount === 0
      ? "No pending requests"
      : pendingCount === 1
        ? "1 request needs review"
        : `${pendingCount} requests need review`;

  return (
    <div>
      <div class="sandbox-hero">
        <div class="d-flex align-items-center gap-3 mb-2">
          <h3 class="mb-0">{sandbox.name}</h3>
          <span class={`badge ${badgeClass("phase", sandbox.phase)}`}>{sandbox.phase}</span>
          {wsState !== "connected" && (
            <span class="badge text-bg-warning ms-1">
              <i class="bi bi-wifi-off" />{" "}
              {wsState === "reconnecting" ? "Reconnecting…" : wsState === "failed" ? "Disconnected" : ""}
            </span>
          )}
        </div>
        <div class="sandbox-meta">
          <span class="font-monospace">{sandbox.image || "Default image"}</span>
          {sandbox.created_at_ms && <span>{formatTimestamp(sandbox.created_at_ms)}</span>}
          {sandbox.gpu && (
            <span>
              <i class="bi bi-gpu-card text-info me-1" />
              GPU
            </span>
          )}
        </div>
      </div>

      <div class="row g-3 mb-4">
        <div class="col-md-4">
          <a
            href={gwUrl(`/sandboxes/${name}/policy`)}
            class="card text-decoration-none policy-overview-card sg-card-themed h-100"
          >
            <div class="card-body">
              <div class="d-flex align-items-center mb-2">
                <i class="bi bi-shield-lock text-info me-2" />
                <h6 class="mb-0">Policy</h6>
              </div>
              <div class="fs-2 fw-bold mb-1">{networkCount}</div>
              <span class="text-muted small">
                {networkLabel} · v{sandbox.current_policy_version ?? "?"}
              </span>
            </div>
            <div class="card-footer border-0 pt-0 small bg-transparent">
              Manage <i class="bi bi-arrow-right" />
            </div>
          </a>
        </div>
        <div class="col-md-4">
          <a
            href={gwUrl(`/sandboxes/${name}/approvals`)}
            class="card text-decoration-none policy-overview-card sg-card-themed h-100"
          >
            <div class="card-body">
              <div class="d-flex align-items-center mb-2">
                <i
                  class={`bi bi-check-circle me-2 ${pendingCount > 0 ? "text-warning" : "text-success"}`}
                />
                <h6 class="mb-0">Approvals</h6>
              </div>
              <div class={`fs-2 fw-bold mb-1 ${pendingCount > 0 ? "text-warning" : ""}`}>
                {pendingCount}
              </div>
              <span class="text-muted small">{pendingLabel}</span>
              {securityFlaggedCount > 0 && (
                <div class="small text-danger mt-1">
                  <i class="bi bi-exclamation-triangle me-1" />
                  <span>{securityFlaggedCount} security-flagged</span>
                </div>
              )}
              {lastAnalyzedAtMs > 0 && (
                <div class="small text-muted mt-1">
                  <i class="bi bi-clock-history me-1" />
                  Last analyzed <span>{formatTimestamp(lastAnalyzedAtMs)}</span>
                </div>
              )}
            </div>
            <div class="card-footer border-0 pt-0 small bg-transparent">
              <span>{pendingCount > 0 ? "Review" : "View history"}</span>{" "}
              <i class="bi bi-arrow-right" />
            </div>
          </a>
        </div>
        <div class="col-md-4">
          <a
            href={gwUrl(`/sandboxes/${name}/logs`)}
            class="card text-decoration-none policy-overview-card sg-card-themed h-100"
          >
            <div class="card-body">
              <div class="d-flex align-items-center mb-2">
                <i class="bi bi-journal-text text-muted me-2" />
                <h6 class="mb-0">Logs</h6>
              </div>
              <span class="text-muted small">Live log stream and history</span>
            </div>
            <div class="card-footer border-0 pt-0 small bg-transparent">
              View <i class="bi bi-arrow-right" />
            </div>
          </a>
        </div>
      </div>

      <fieldset class="sg-fieldset mb-4">
        <legend class="sg-legend">Metadata</legend>
        <div class="row g-3">
          <div class="col-12">
            <label class="form-label small text-muted">
              <i class="bi bi-card-text me-1" />
              Description
            </label>
            {isOperator ? (
              <input
                type="text"
                class="form-control form-control-sm"
                placeholder="e.g. ML training sandbox for team-alpha"
                maxLength={1000}
                value={metaDescription}
                onInput={(e) => setMetaDescription((e.target as HTMLInputElement).value)}
              />
            ) : (
              <div class="form-control-plaintext small">{sandbox.description || "—"}</div>
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
                    {isOperator && (
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
            {isOperator && metaLabels.length < 20 && (
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
            {!isOperator && metaLabels.length === 0 && (
              <span class="text-muted fst-italic small">No labels</span>
            )}
          </div>
        </div>
        {isOperator && (
          <div class="d-flex align-items-center gap-2 pt-2">
            <button class="btn btn-primary btn-sm" onClick={() => void saveMeta()} disabled={saving}>
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
      </fieldset>

      <fieldset class="sg-fieldset mb-4">
        <legend class="sg-legend">
          <i class="bi bi-plug me-1" />
          Attached Providers
        </legend>
        {attachError && <div class="alert alert-danger py-2 small">{attachError}</div>}
        {attachedProviders.length === 0 && (
          <div class="text-muted fst-italic small mb-2">No providers attached to this sandbox.</div>
        )}
        {attachedProviders.length > 0 && (
          <div class="d-flex flex-wrap gap-2 mb-2">
            {attachedProviders.map((prov) => (
              <span
                key={prov.name}
                class="badge text-bg-light border d-inline-flex align-items-center gap-1"
              >
                <i class="bi bi-key text-info" />
                <span class="font-monospace">{prov.name}</span>
                <span class="text-muted small">({prov.type})</span>
                {isOperator && (
                  <button
                    type="button"
                    class="btn-close btn-close-sm ms-1 sg-fs-xxs"
                    title="Detach"
                    onClick={() => void detachProvider(prov.name)}
                  />
                )}
              </span>
            ))}
          </div>
        )}
        {isOperator && (
          <div class="input-group input-group-sm sg-mw-400" onClick={() => void loadAvailableProviders()}>
            <select
              class="form-select"
              disabled={attachBusy}
              value={attachProviderName}
              onChange={(e) => setAttachProviderName((e.target as HTMLSelectElement).value)}
            >
              <option value="">Select provider…</option>
              {attachableProviders.map((prov) => (
                <option key={prov.name} value={prov.name}>
                  {prov.name} ({prov.type})
                </option>
              ))}
            </select>
            <button
              class="btn btn-outline-success"
              type="button"
              onClick={() => void attachProvider()}
              disabled={!attachProviderName || attachBusy}
            >
              {attachBusy ? (
                <span class="spinner-border spinner-border-sm me-1" />
              ) : (
                <i class="bi bi-plus me-1" />
              )}
              Attach
            </button>
          </div>
        )}
      </fieldset>

      <h6 class="text-muted mb-3">Properties</h6>
      <dl class="row mb-0 small">
        <dt class="col-sm-2 text-muted fw-normal">ID</dt>
        <dd class="col-sm-10 font-monospace">{sandbox.id}</dd>
      </dl>
    </div>
  );
}
