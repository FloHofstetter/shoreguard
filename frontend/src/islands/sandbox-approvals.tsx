/** Draft policy approvals page (island): table flow, quorum voting. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { auth, ensureAuth, hasRole } from "../lib/auth";
import { API, badgeClass, GW } from "../lib/constants";
import { Modal } from "../lib/Modal";
import { showConfirm, showToast } from "../lib/notify";
import { EmptyState, ErrorAlert, Spinner } from "../lib/widgets";

const HISTORY_EVENT_TYPES = [
  { type: "denial_detected", label: "Denial detected", badge: "text-bg-warning" },
  { type: "analysis_cycle", label: "Analysis cycle", badge: "text-bg-secondary" },
  { type: "approved", label: "Approved", badge: "text-bg-success" },
  { type: "rejected", label: "Rejected", badge: "text-bg-danger" },
  { type: "edited", label: "Edited", badge: "text-bg-info" },
  { type: "undone", label: "Undone", badge: "text-bg-info" },
  { type: "cleared", label: "Cleared", badge: "text-bg-secondary" },
];

interface Endpoint {
  host: string;
  port: number;
}

interface DenialContext {
  persistent?: boolean;
  ancestors?: string[];
  binary_sha256?: string;
  l7_request_samples?: { method: string; path: string; decision: string; count: number }[];
}

interface Chunk {
  id: string;
  status: string;
  rule_name?: string;
  binary?: string;
  rationale?: string;
  security_notes?: string;
  stage?: string;
  confidence?: number;
  hit_count?: number;
  first_seen_ms?: number;
  last_seen_ms?: number;
  denial_summary_ids?: string[];
  denial_context?: DenialContext | null;
  proposed_rule?: { endpoints?: Endpoint[] } & Record<string, unknown>;
}

interface Workflow {
  required_approvals: number;
  required_roles?: string[];
  distinct_actors?: boolean;
  escalation_timeout_minutes?: number | null;
}

interface Decision {
  actor: string;
  role?: string;
  decision: string;
}

interface HistoryEntry {
  event_type?: string;
  timestamp_ms?: number;
  chunk_id?: string;
  description?: string;
}

function isSecurityFlagged(chunk: Chunk): boolean {
  return Boolean(chunk.security_notes?.trim());
}

function formatTimestamp(ms: number | undefined): string {
  return ms ? new Date(ms).toLocaleString() : "—";
}

// ── Workflow config modal ────────────────────────────────────────────

function WorkflowConfigModal({ sandboxName, current, onDone, onClose }: {
  sandboxName: string;
  current: Workflow | null;
  onDone: (updated: Workflow | null) => void;
  onClose: () => void;
}) {
  const [required, setRequired] = useState(current?.required_approvals ?? 2);
  const [roles, setRoles] = useState((current?.required_roles ?? []).join(", "));
  const [distinct, setDistinct] = useState(current?.distinct_actors ?? true);
  const [escalate, setEscalate] = useState(
    current?.escalation_timeout_minutes != null ? String(current.escalation_timeout_minutes) : "",
  );
  const [error, setError] = useState("");

  const save = async () => {
    const body = {
      required_approvals: required,
      required_roles: roles
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      distinct_actors: distinct,
      escalation_timeout_minutes: escalate ? parseInt(escalate, 10) : null,
    };
    try {
      const result = await apiFetch<Workflow>(
        `${API}/sandboxes/${sandboxName}/approval-workflow`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      showToast("Workflow saved.", "success");
      onDone(result);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const disable = async () => {
    try {
      await apiFetch(`${API}/sandboxes/${sandboxName}/approval-workflow`, { method: "DELETE" });
      showToast("Workflow disabled.", "warning");
      onDone(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Modal
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-people-fill me-2" />
          Multi-Stage Approval Workflow
        </span>
      }
      footer={
        <>
          {current && (
            <button class="btn btn-outline-danger me-auto" onClick={() => void disable()}>
              <i class="bi bi-trash me-1" />
              Disable workflow
            </button>
          )}
          <button class="btn btn-primary" onClick={() => void save()}>
            <i class="bi bi-check me-1" />
            Save
          </button>
        </>
      }
    >
      <p class="text-muted small">
        Configure how many distinct approvals are required before a draft chunk is approved
        upstream.
      </p>
      <div class="mb-3">
        <label class="form-label">Required approvals</label>
        <input
          type="number"
          min={1}
          max={20}
          class="form-control"
          value={required}
          onInput={(e) => setRequired(parseInt((e.target as HTMLInputElement).value, 10) || 1)}
        />
      </div>
      <div class="mb-3">
        <label class="form-label">Allowed voter roles (comma-separated, empty = any)</label>
        <input
          type="text"
          class="form-control"
          placeholder="admin, operator"
          value={roles}
          onInput={(e) => setRoles((e.target as HTMLInputElement).value)}
        />
      </div>
      <div class="form-check mb-3">
        <input
          class="form-check-input"
          type="checkbox"
          id="wf-distinct"
          checked={distinct}
          onChange={(e) => setDistinct((e.target as HTMLInputElement).checked)}
        />
        <label class="form-check-label" for="wf-distinct">
          Distinct actors (same user cannot vote twice)
        </label>
      </div>
      <div class="mb-3">
        <label class="form-label">Escalation timeout (minutes, empty = off)</label>
        <input
          type="number"
          min={1}
          max={10080}
          class="form-control"
          value={escalate}
          onInput={(e) => setEscalate((e.target as HTMLInputElement).value)}
        />
      </div>
      {error && <div class="text-danger small">{error}</div>}
    </Modal>
  );
}

// ── Edit chunk modal ─────────────────────────────────────────────────

function EditChunkModal({ sandboxName, chunk, onSaved, onClose }: {
  sandboxName: string;
  chunk: Chunk;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [json, setJson] = useState(JSON.stringify(chunk.proposed_rule ?? {}, null, 2));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    let proposed_rule: unknown;
    try {
      proposed_rule = JSON.parse(json);
    } catch (e) {
      setError(`Invalid JSON: ${(e as Error).message}`);
      return;
    }
    setSaving(true);
    setError("");
    try {
      await apiFetch(`${API}/sandboxes/${sandboxName}/approvals/${chunk.id}/edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proposed_rule }),
      });
      showToast("Proposed rule updated.", "success");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      size="lg"
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-pencil me-2" />
          Edit Proposed Rule
        </span>
      }
      footer={
        <button class="btn btn-success" onClick={() => void save()} disabled={saving}>
          <i class="bi bi-check me-1" />
          Save
        </button>
      }
    >
      <p class="text-muted small mb-2">
        Edit the proposed rule JSON. Changes are saved as a new proposal.
      </p>
      <textarea
        class="form-control font-monospace"
        rows={14}
        spellcheck={false}
        value={json}
        onInput={(e) => setJson((e.target as HTMLTextAreaElement).value)}
      />
      {error && (
        <div class="text-danger small mt-2">
          <i class="bi bi-x-circle me-1" />
          {error}
        </div>
      )}
    </Modal>
  );
}

// ── History modal ────────────────────────────────────────────────────

function HistoryModal({ sandboxName, onClose }: { sandboxName: string; onClose: () => void }) {
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const [error, setError] = useState("");
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  useEffect(() => {
    apiFetch<HistoryEntry[]>(`${API}/sandboxes/${sandboxName}/approvals/history`)
      .then(setHistory)
      .catch((e: Error) => setError(e.message));
  }, [sandboxName]);

  const counts: Record<string, number> = {};
  for (const entry of history ?? []) {
    const t = entry.event_type || "unknown";
    counts[t] = (counts[t] || 0) + 1;
  }

  return (
    <Modal
      size="lg"
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-clock-history me-2" />
          Approval History
        </span>
      }
    >
      {error && <ErrorAlert message={error} />}
      {!error && history === null && <Spinner message="Loading history..." />}
      {!error && history !== null && history.length === 0 && (
        <EmptyState icon="clock-history" message="No approval history yet." />
      )}
      {!error && history !== null && history.length > 0 && (
        <div>
          <div class="mb-3 d-flex flex-wrap align-items-center">
            <span class="text-muted small me-2">Filter:</span>
            {HISTORY_EVENT_TYPES.filter((t) => counts[t.type]).map((t) => (
              <button
                key={t.type}
                type="button"
                class={`btn btn-sm me-1 mb-1 history-chip ${t.badge} ${hidden.has(t.type) ? "opacity-50" : ""}`}
                onClick={() => {
                  const next = new Set(hidden);
                  if (next.has(t.type)) {
                    next.delete(t.type);
                  } else {
                    next.add(t.type);
                  }
                  setHidden(next);
                }}
              >
                {t.label}
                <span class="badge bg-dark ms-1">{counts[t.type]}</span>
              </button>
            ))}
          </div>
          <div class="table-responsive">
            <table class="table table-striped table-sm align-middle">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Timestamp</th>
                  <th>Chunk</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {history
                  .filter((entry) => !hidden.has(entry.event_type || "unknown"))
                  .map((entry, i) => {
                    const typeInfo = HISTORY_EVENT_TYPES.find((t) => t.type === entry.event_type);
                    return (
                      <tr key={i}>
                        <td>
                          <span class={`badge ${typeInfo?.badge ?? "text-bg-secondary"}`}>
                            {typeInfo?.label ?? entry.event_type ?? "unknown"}
                          </span>
                        </td>
                        <td class="text-muted small">{formatTimestamp(entry.timestamp_ms)}</td>
                        <td class="font-monospace small">{entry.chunk_id || "—"}</td>
                        <td class="text-muted small">{entry.description || "—"}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Modal>
  );
}

// ── Approve-all confirm (security-flagged variant) ───────────────────

function ApproveAllConfirmModal({ flagged, onDecide, onClose }: {
  flagged: Chunk[];
  onDecide: (includeFlagged: boolean) => void;
  onClose: () => void;
}) {
  const [include, setInclude] = useState(false);
  return (
    <Modal
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-shield-exclamation text-warning me-2" />
          Security-Flagged Chunks
        </span>
      }
      footer={
        <button class="btn btn-success" onClick={() => onDecide(include)}>
          <i class="bi bi-check-all me-1" />
          Approve All
        </button>
      }
    >
      <p class="mb-2">{flagged.length} security-flagged chunk(s) require review:</p>
      <ul class="small mb-3">
        {flagged.map((c) => (
          <li key={c.id} class="mb-1">
            <strong>{c.rule_name}</strong>:{" "}
            <span class="text-danger small">{c.security_notes}</span>
          </li>
        ))}
      </ul>
      <div class="form-check">
        <input
          class="form-check-input"
          type="checkbox"
          id="includeSecFlagged"
          checked={include}
          onChange={(e) => setInclude((e.target as HTMLInputElement).checked)}
        />
        <label class="form-check-label" for="includeSecFlagged">
          Include security-flagged chunks in approval
        </label>
      </div>
    </Modal>
  );
}

// ── Main page ────────────────────────────────────────────────────────

export default function SandboxApprovalsPage({ name }: { name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [rollingSummary, setRollingSummary] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [sortPersistentFirst, setSortPersistentFirst] = useState(() => {
    try {
      return localStorage.getItem("sg-approvals-sort-persistent") === "1";
    } catch {
      return false;
    }
  });
  const [filterSecurityFlagged, setFilterSecurityFlagged] = useState(false);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [decisionsByChunk, setDecisionsByChunk] = useState<Record<string, Decision[]>>({});
  const [highlightChunkId, setHighlightChunkId] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [isOperator, setIsOperator] = useState(false);

  const [workflowModalOpen, setWorkflowModalOpen] = useState(false);
  const [editChunk, setEditChunk] = useState<Chunk | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [approveAllFlagged, setApproveAllFlagged] = useState<Chunk[] | null>(null);

  const refreshDecisions = async (wf: Workflow | null, currentChunks: Chunk[]) => {
    if (!wf) return;
    const pending = currentChunks.filter((c) => c.status === "pending");
    const next: Record<string, Decision[]> = {};
    await Promise.all(
      pending.map(async (c) => {
        try {
          const r = await apiFetch<{ decisions?: Decision[] }>(
            `${API}/sandboxes/${name}/approvals/${c.id}/decisions`,
          );
          next[c.id] = r.decisions ?? [];
        } catch {
          next[c.id] = [];
        }
      }),
    );
    setDecisionsByChunk(next);
  };

  const load = async (wf: Workflow | null = workflow) => {
    setLoading(true);
    setError("");
    try {
      const data = await apiFetch<{
        chunks?: Chunk[];
        rolling_summary?: string;
      }>(`${API}/sandboxes/${name}/approvals`);
      const loaded = data.chunks ?? [];
      setChunks(loaded);
      setRollingSummary(data.rolling_summary ?? "");
      await refreshDecisions(wf, loaded);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const applyHashCrossLink = (currentChunks: Chunk[]) => {
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash) return;
    const params: Record<string, string> = {};
    for (const part of hash.split("&")) {
      const [k, v] = part.split("=", 2);
      if (k) params[decodeURIComponent(k)] = v ? decodeURIComponent(v) : "";
    }
    if (!params.binary && !params.host) return;
    const match = currentChunks.find((c) => {
      if (params.binary && c.binary !== params.binary) return false;
      if (params.host) {
        const endpoints = c.proposed_rule?.endpoints ?? [];
        if (!endpoints.some((ep) => ep.host === params.host)) return false;
      }
      return true;
    });
    if (!match) return;
    setHighlightChunkId(match.id);
    setExpanded((prev) => ({ ...prev, [match.id]: true }));
    requestAnimationFrame(() => {
      document
        .getElementById(`chunk-row-${match.id}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => setHighlightChunkId(""), 3500);
    });
  };

  useEffect(() => {
    void (async () => {
      await ensureAuth();
      setIsAdmin(hasRole("admin"));
      setIsOperator(hasRole("operator"));
      let wf: Workflow | null = null;
      try {
        const data = await apiFetch<Workflow>(`${API}/sandboxes/${name}/approval-workflow`);
        wf = data?.required_approvals ? data : null;
      } catch {
        wf = null;
      }
      setWorkflow(wf);
      setLoading(true);
      try {
        const data = await apiFetch<{ chunks?: Chunk[]; rolling_summary?: string }>(
          `${API}/sandboxes/${name}/approvals`,
        );
        const loaded = data.chunks ?? [];
        setChunks(loaded);
        setRollingSummary(data.rolling_summary ?? "");
        await refreshDecisions(wf, loaded);
        applyHashCrossLink(loaded);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    })();

    const onUpdate = (ev: Event) => {
      const detail = (ev as CustomEvent).detail;
      if (detail?.sandboxName === name || detail?.sandbox_name === name) void load();
    };
    document.addEventListener("sg:approvals-update", onUpdate);
    return () => document.removeEventListener("sg:approvals-update", onUpdate);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  useEffect(() => {
    try {
      localStorage.setItem("sg-approvals-sort-persistent", sortPersistentFirst ? "1" : "0");
    } catch {
      // ignore
    }
  }, [sortPersistentFirst]);

  const pendingCount = chunks.filter((c) => c.status === "pending").length;
  const flaggedPending = chunks.filter((c) => c.status === "pending" && isSecurityFlagged(c));

  let sortedChunks = filterSecurityFlagged ? chunks.filter(isSecurityFlagged) : chunks;
  if (sortPersistentFirst) {
    sortedChunks = [...sortedChunks].sort((a, b) => {
      const ap = a.denial_context?.persistent ? 1 : 0;
      const bp = b.denial_context?.persistent ? 1 : 0;
      return bp - ap;
    });
  }

  const actorId = auth.value.email ?? "";
  const voteCount = (chunkId: string) =>
    (decisionsByChunk[chunkId] ?? []).filter((d) => d.decision === "approve").length;
  const hasVoted = (chunkId: string) =>
    (decisionsByChunk[chunkId] ?? []).some((d) => d.actor === actorId);

  const hasDetail = (chunk: Chunk) =>
    Boolean(
      chunk.rationale ||
        chunk.security_notes ||
        chunk.stage ||
        (chunk.denial_summary_ids ?? []).length > 0 ||
        chunk.binary ||
        chunk.denial_context,
    );

  const formatSeen = (chunk: Chunk) => {
    const last = chunk.last_seen_ms ?? 0;
    if (!chunk.first_seen_ms && !last) return "—";
    const lastStr = last ? formatTimestamp(last) : "—";
    if ((chunk.hit_count ?? 0) > 1) return `${chunk.hit_count}×, last ${lastStr}`;
    return lastStr;
  };

  const goToLogs = (chunk: Chunk) => {
    const parts: string[] = [];
    if (chunk.binary) parts.push(chunk.binary);
    const endpoints = chunk.proposed_rule?.endpoints ?? [];
    if (endpoints.length > 0 && endpoints[0].host) parts.push(endpoints[0].host);
    const filter = parts.join(" ");
    window.location.href = `/gateways/${GW}/sandboxes/${name}/logs${
      filter ? `?text=${encodeURIComponent(filter)}` : ""
    }`;
  };

  const approve = async (chunkId: string) => {
    try {
      const result = await apiFetch<{ status?: string; votes?: number; needed?: number }>(
        `${API}/sandboxes/${name}/approvals/${chunkId}/approve`,
        { method: "POST" },
      );
      if (result?.status === "pending") {
        const remaining = Math.max(0, (result.needed ?? 0) - (result.votes ?? 0));
        showToast(
          `Vote cast — ${result.votes}/${result.needed} approvals (${remaining} more needed).`,
          "info",
        );
      } else {
        showToast("Chunk approved.", "success");
      }
      await load();
    } catch (e) {
      showToast(`Approve failed: ${(e as Error).message}`, "danger");
    }
  };

  const reject = async (chunkId: string) => {
    try {
      await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/reject`, { method: "POST" });
      showToast("Chunk rejected.", "warning");
      await load();
    } catch (e) {
      showToast(`Reject failed: ${(e as Error).message}`, "danger");
    }
  };

  const undo = async (chunkId: string) => {
    try {
      await apiFetch(`${API}/sandboxes/${name}/approvals/${chunkId}/undo`, { method: "POST" });
      showToast("Approval undone.", "warning");
      await load();
    } catch (e) {
      showToast(`Undo failed: ${(e as Error).message}`, "danger");
    }
  };

  const submitApproveAll = async (includeSecurityFlagged: boolean) => {
    try {
      await apiFetch(`${API}/sandboxes/${name}/approvals/approve-all`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_security_flagged: includeSecurityFlagged }),
      });
      showToast(
        includeSecurityFlagged
          ? "All pending chunks approved (including security-flagged)."
          : "All pending chunks approved (security-flagged excluded).",
        "success",
      );
      await load();
    } catch (e) {
      showToast(`Approve all failed: ${(e as Error).message}`, "danger");
    }
  };

  const approveAll = async () => {
    if (flaggedPending.length > 0) {
      setApproveAllFlagged(flaggedPending);
      return;
    }
    const confirmed = await showConfirm("Approve all pending recommendations?", {
      icon: "check-all",
      iconColor: "text-success",
      btnClass: "btn-success",
      btnLabel: "Approve All",
    });
    if (!confirmed) return;
    await submitApproveAll(false);
  };

  const clearAll = async () => {
    const confirmed = await showConfirm("Clear all pending recommendations?", {
      icon: "trash",
      iconColor: "text-warning",
      btnClass: "btn-warning",
      btnLabel: "Clear All",
    });
    if (!confirmed) return;
    try {
      await apiFetch(`${API}/sandboxes/${name}/approvals/clear`, { method: "POST" });
      showToast("All chunks cleared.", "success");
      await load();
    } catch (e) {
      showToast(`Clear failed: ${(e as Error).message}`, "danger");
    }
  };

  if (loading && chunks.length === 0) return <Spinner message="Loading approvals..." />;
  if (error) return <ErrorAlert message={error} />;

  return (
    <div id="approvals-content">
      {chunks.length === 0 && (
        <div class="text-center text-muted py-4">
          <i class="bi bi-shield-check fs-1 d-block mb-2" />
          <p>No draft policy recommendations.</p>
        </div>
      )}

      {chunks.length > 0 && (
        <div>
          {workflow && (
            <div class="alert alert-primary d-flex align-items-center py-2 mb-3">
              <i class="bi bi-people-fill me-2" />
              <span>
                Multi-stage approval active — <strong>{workflow.required_approvals}</strong>{" "}
                distinct vote(s) required
                {(workflow.required_roles ?? []).length > 0 && (
                  <span> (roles: {workflow.required_roles!.join(", ")})</span>
                )}
              </span>
              {isAdmin && (
                <button
                  class="btn btn-sm btn-outline-primary ms-auto"
                  onClick={() => setWorkflowModalOpen(true)}
                >
                  <i class="bi bi-gear me-1" />
                  Configure
                </button>
              )}
            </div>
          )}

          {flaggedPending.length > 0 && (
            <div class="alert alert-warning d-flex align-items-center py-2 mb-3">
              <i class="bi bi-shield-exclamation me-2" />
              <span>{flaggedPending.length} security-flagged chunk(s) require review</span>
              <button
                class="btn btn-sm btn-outline-warning ms-auto"
                onClick={() => setFilterSecurityFlagged(true)}
              >
                Show only flagged
              </button>
            </div>
          )}

          <div class="d-flex justify-content-between align-items-center mb-3">
            <div class="d-flex align-items-center gap-2">
              <span class="text-muted">
                {pendingCount} pending of {chunks.length} total
              </span>
              {chunks.some(isSecurityFlagged) && (
                <button
                  class={`btn btn-sm ${filterSecurityFlagged ? "btn-warning" : "btn-outline-secondary"}`}
                  onClick={() => setFilterSecurityFlagged(!filterSecurityFlagged)}
                >
                  <i class="bi bi-shield-exclamation me-1" />
                  Security-flagged
                  <span class="badge bg-dark ms-1">{flaggedPending.length}</span>
                </button>
              )}
            </div>
            <div class="btn-group btn-group-sm">
              <button
                class="btn btn-outline-secondary"
                title="History"
                onClick={() => setHistoryOpen(true)}
              >
                <i class="bi bi-clock-history me-1" />
                History
              </button>
              {isAdmin && !workflow && (
                <button
                  class="btn btn-outline-primary"
                  title="Configure workflow"
                  onClick={() => setWorkflowModalOpen(true)}
                >
                  <i class="bi bi-people-fill me-1" />
                  Workflow
                </button>
              )}
              {isOperator && (
                <button class="btn btn-success" onClick={() => void approveAll()}>
                  <i class="bi bi-check-all me-1" />
                  Approve All
                </button>
              )}
              {isOperator && (
                <button class="btn btn-outline-secondary" onClick={() => void clearAll()}>
                  Clear All
                </button>
              )}
              <button
                class={`btn btn-outline-secondary ${sortPersistentFirst ? "active" : ""}`}
                title="Sort persistent denials first"
                onClick={() => setSortPersistentFirst(!sortPersistentFirst)}
              >
                <i class="bi bi-arrow-repeat me-1" />
                Persistent first
              </button>
            </div>
          </div>

          {rollingSummary && (
            <div class="alert alert-info small py-2 mb-3">
              <i class="bi bi-lightbulb me-1" />
              <span>{rollingSummary}</span>
            </div>
          )}

          <div class="table-responsive">
            <table class="table table-striped table-hover table-sm align-middle">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Rule</th>
                  <th>Binary</th>
                  <th>Endpoints</th>
                  <th class="text-end">Hits</th>
                  <th>Seen</th>
                  <th class="text-center">Confidence</th>
                  <th class="text-end">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sortedChunks.map((chunk) => {
                  const endpoints = chunk.proposed_rule?.endpoints ?? [];
                  const endpointShown = endpoints.slice(0, 2);
                  const endpointMore = Math.max(0, endpoints.length - 2);
                  const confidencePct = Math.round((chunk.confidence ?? 0) * 100);
                  const ctx = chunk.denial_context;
                  return (
                    <tr
                      key={chunk.id}
                      id={`chunk-row-${chunk.id}`}
                      class={highlightChunkId === chunk.id ? "table-warning" : ""}
                    >
                      <td>
                        <span class={`badge ${badgeClass("approval", chunk.status)}`}>
                          {chunk.status}
                        </span>
                        {isSecurityFlagged(chunk) && (
                          <span class="badge text-bg-danger ms-1" title="Security flagged">
                            <i class="bi bi-shield-exclamation" />
                          </span>
                        )}
                      </td>
                      <td
                        class={`sg-cursor-pointer ${hasDetail(chunk) ? "table-clickable" : ""}`}
                        onClick={() =>
                          hasDetail(chunk) &&
                          setExpanded({ ...expanded, [chunk.id]: !expanded[chunk.id] })
                        }
                      >
                        <strong>{chunk.rule_name}</strong>
                        {hasDetail(chunk) && (
                          <i
                            class={`bi ms-1 small ${expanded[chunk.id] ? "bi-chevron-down" : "bi-chevron-right"}`}
                          />
                        )}
                        {expanded[chunk.id] && (
                          <div class="mt-2">
                            {chunk.rationale && (
                              <p class="small text-muted mb-1">
                                <i class="bi bi-chat-quote me-1" />
                                <span>{chunk.rationale}</span>
                              </p>
                            )}
                            {chunk.security_notes && (
                              <div class="alert alert-warning small py-1 px-2 mb-2">
                                <i class="bi bi-exclamation-triangle me-1" />
                                <span>{chunk.security_notes}</span>
                              </div>
                            )}
                            {chunk.stage && (
                              <div class="small text-muted mb-1">
                                <span class="fw-semibold">Stage:</span> <code>{chunk.stage}</code>
                              </div>
                            )}
                            {(chunk.denial_summary_ids ?? []).length > 0 && (
                              <div class="small text-muted mb-2">
                                <span class="fw-semibold">Related denials:</span>{" "}
                                {chunk.denial_summary_ids!.map((id) => (
                                  <code key={id} class="me-1 px-1 bg-body-secondary rounded small">
                                    {id}
                                  </code>
                                ))}
                              </div>
                            )}
                            {(ctx?.ancestors ?? []).length > 0 && (
                              <div class="small text-muted mb-1">
                                <span class="fw-semibold">Process chain:</span>{" "}
                                <code>{ctx!.ancestors!.join(" → ")}</code>
                              </div>
                            )}
                            {ctx?.binary_sha256 && (
                              <div class="small text-muted mb-1">
                                <span class="fw-semibold">SHA256:</span>{" "}
                                <code
                                  class="font-monospace px-1 bg-body-secondary rounded small sg-cursor-pointer"
                                  title={ctx.binary_sha256}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    void navigator.clipboard.writeText(ctx.binary_sha256!);
                                  }}
                                >
                                  {ctx.binary_sha256.slice(0, 16)}…
                                </code>
                                <i class="bi bi-clipboard ms-1 small text-muted" />
                              </div>
                            )}
                            {ctx?.persistent && (
                              <div class="small mb-1">
                                <span class="badge text-bg-warning">
                                  <i class="bi bi-arrow-repeat me-1" />
                                  Persistent
                                </span>
                              </div>
                            )}
                            {(ctx?.l7_request_samples ?? []).length > 0 && (
                              <div class="small mb-2">
                                <span class="fw-semibold text-muted">L7 Request Samples:</span>
                                <table
                                  class="table table-sm table-bordered mt-1 mb-0"
                                  style="max-width:500px"
                                >
                                  <thead>
                                    <tr class="small text-muted">
                                      <th>Method</th>
                                      <th>Path</th>
                                      <th>Decision</th>
                                      <th class="text-end">Count</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {ctx!.l7_request_samples!.map((sample) => (
                                      <tr key={`${sample.path}${sample.method}`} class="small">
                                        <td>
                                          <code>{sample.method}</code>
                                        </td>
                                        <td class="font-monospace">{sample.path}</td>
                                        <td>
                                          <span
                                            class={`badge ${
                                              sample.decision === "allow"
                                                ? "text-bg-success"
                                                : "text-bg-danger"
                                            }`}
                                          >
                                            {sample.decision}
                                          </span>
                                        </td>
                                        <td class="text-end">{sample.count}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                            <div>
                              <button
                                class="btn btn-sm btn-outline-secondary"
                                title="Open sandbox logs filtered by binary/host"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  goToLogs(chunk);
                                }}
                              >
                                <i class="bi bi-arrow-up-right-square me-1" />
                                View in logs
                              </button>
                            </div>
                          </div>
                        )}
                      </td>
                      <td class="font-monospace small text-muted">{chunk.binary || "—"}</td>
                      <td>
                        {endpointShown.map((ep) => (
                          <span key={`${ep.host}:${ep.port}`} class="badge endpoint-badge me-1">
                            {ep.host}:{ep.port}
                          </span>
                        ))}
                        {endpointMore > 0 && (
                          <span class="badge text-bg-secondary">+{endpointMore}</span>
                        )}
                        {endpoints.length === 0 && <span class="text-muted">—</span>}
                      </td>
                      <td class="text-end">
                        {(chunk.hit_count ?? 0) > 1 ? chunk.hit_count : "—"}
                      </td>
                      <td class="small text-muted">{formatSeen(chunk)}</td>
                      <td class="text-center">
                        {(chunk.confidence ?? 0) > 0 ? (
                          <div
                            class="progress confidence-bar d-inline-flex"
                            title={`${confidencePct}%`}
                          >
                            <div class="progress-bar bg-info" style={`width:${confidencePct}%`} />
                          </div>
                        ) : (
                          <span class="text-muted">—</span>
                        )}
                      </td>
                      <td class="text-end">
                        {workflow && chunk.status === "pending" && (
                          <div
                            class="small text-muted mb-1"
                            title={(decisionsByChunk[chunk.id] ?? [])
                              .map(
                                (d) => `${d.actor}${d.role ? ` (${d.role})` : ""}: ${d.decision}`,
                              )
                              .join("\n")}
                          >
                            <i class="bi bi-people-fill me-1" />
                            <span>
                              {voteCount(chunk.id)}/{workflow.required_approvals}
                            </span>
                            {hasVoted(chunk.id) && (
                              <span class="badge text-bg-info ms-1">voted</span>
                            )}
                          </div>
                        )}
                        {chunk.status === "pending" && isOperator && (
                          <div class="btn-group btn-group-sm">
                            <button
                              class="btn btn-outline-secondary"
                              title="Edit"
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditChunk(chunk);
                              }}
                            >
                              <i class="bi bi-pencil" />
                            </button>
                            <button
                              class="btn btn-success"
                              disabled={Boolean(workflow) && hasVoted(chunk.id)}
                              title={workflow ? "Vote to approve" : "Approve"}
                              onClick={(e) => {
                                e.stopPropagation();
                                void approve(chunk.id);
                              }}
                            >
                              <i class="bi bi-check" />
                            </button>
                            <button
                              class="btn btn-outline-danger"
                              title="Reject"
                              onClick={(e) => {
                                e.stopPropagation();
                                void reject(chunk.id);
                              }}
                            >
                              <i class="bi bi-x" />
                            </button>
                          </div>
                        )}
                        {chunk.status === "approved" && isOperator && (
                          <button
                            class="btn btn-outline-secondary btn-sm"
                            title="Undo"
                            onClick={(e) => {
                              e.stopPropagation();
                              void undo(chunk.id);
                            }}
                          >
                            <i class="bi bi-arrow-counterclockwise" />
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {workflowModalOpen && (
        <WorkflowConfigModal
          sandboxName={name}
          current={workflow}
          onDone={(updated) => {
            setWorkflowModalOpen(false);
            setWorkflow(updated);
            void load(updated);
          }}
          onClose={() => setWorkflowModalOpen(false)}
        />
      )}
      {editChunk && (
        <EditChunkModal
          sandboxName={name}
          chunk={editChunk}
          onSaved={() => {
            setEditChunk(null);
            void load();
          }}
          onClose={() => setEditChunk(null)}
        />
      )}
      {historyOpen && <HistoryModal sandboxName={name} onClose={() => setHistoryOpen(false)} />}
      {approveAllFlagged && (
        <ApproveAllConfirmModal
          flagged={approveAllFlagged}
          onDecide={(include) => {
            setApproveAllFlagged(null);
            void submitApproveAll(include);
          }}
          onClose={() => setApproveAllFlagged(null)}
        />
      )}
    </div>
  );
}
