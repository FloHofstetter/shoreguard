/** Boot hooks page (island): CRUD + manual run for pre/post-create hooks. */

import { useEffect, useState } from "preact/hooks";

import { ensureAuth, hasRole } from "../lib/auth";
import { GW } from "../lib/constants";
import { Modal } from "../lib/Modal";
import { showConfirm } from "../lib/notify";
import { Spinner } from "../lib/widgets";

const PHASES = ["pre_create", "post_create"] as const;
type Phase = (typeof PHASES)[number];

interface Hook {
  id: number;
  name: string;
  phase: Phase;
  command: string;
  workdir?: string;
  env?: Record<string, string>;
  timeout_seconds: number;
  enabled: boolean;
  continue_on_failure: boolean;
  order: number;
  last_status?: string | null;
  last_output?: string | null;
}

interface Editing {
  id: number | null;
  name: string;
  phase: Phase;
  command: string;
  workdir: string;
  envText: string;
  timeout_seconds: number;
  enabled: boolean;
  continue_on_failure: boolean;
}

function emptyEditing(phase: Phase): Editing {
  return {
    id: null,
    name: "",
    phase,
    command: "",
    workdir: "",
    envText: "",
    timeout_seconds: 30,
    enabled: true,
    continue_on_failure: false,
  };
}

function envTextToObject(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (!text) return out;
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const k = line.slice(0, eq).trim();
    const v = line.slice(eq + 1);
    if (k) out[k] = v;
  }
  return out;
}

function envObjectToText(env: Record<string, string> | undefined): string {
  if (!env) return "";
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

function phaseLabel(phase: Phase): string {
  return phase === "pre_create" ? "Pre-create" : "Post-create";
}

export default function SandboxHooksPage({ name }: { name: string }) {
  const baseUrl = `/api/gateways/${encodeURIComponent(GW)}/sandboxes/${encodeURIComponent(name)}/hooks`;
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [items, setItems] = useState<Hook[]>([]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [editorError, setEditorError] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [isOperator, setIsOperator] = useState(false);

  const loadHooks = async () => {
    setLoading(true);
    try {
      const resp = await fetch(baseUrl);
      if (!resp.ok) {
        console.error("Failed to load hooks:", resp.status);
        return;
      }
      const data = await resp.json();
      setItems(data.items ?? []);
      setLoaded(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadHooks();
    void ensureAuth().then(() => {
      setIsAdmin(hasRole("admin"));
      setIsOperator(hasRole("operator"));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const grouped: Record<Phase, Hook[]> = { pre_create: [], post_create: [] };
  for (const hook of items) {
    if (grouped[hook.phase]) grouped[hook.phase].push(hook);
  }
  for (const phase of PHASES) {
    grouped[phase].sort((a, b) => a.order - b.order || a.id - b.id);
  }

  const saveEditing = async () => {
    if (!editing) return;
    setEditorError("");
    setSaving(true);
    try {
      const env = envTextToObject(editing.envText);
      const common = {
        command: editing.command,
        workdir: editing.workdir,
        env,
        timeout_seconds: editing.timeout_seconds,
        enabled: editing.enabled,
        continue_on_failure: editing.continue_on_failure,
      };
      const resp = editing.id
        ? await fetch(`${baseUrl}/${editing.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(common),
          })
        : await fetch(baseUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: editing.name, phase: editing.phase, ...common }),
          });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        setEditorError(err.detail || `HTTP ${resp.status}`);
        return;
      }
      setEditing(null);
      await loadHooks();
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async (hook: Hook) => {
    const resp = await fetch(`${baseUrl}/${hook.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !hook.enabled }),
    });
    if (!resp.ok) {
      console.error("Failed to toggle hook:", resp.status);
      return;
    }
    await loadHooks();
  };

  const deleteHook = async (hook: Hook) => {
    const confirmed = await showConfirm(`Delete hook '${hook.name}'?`, {
      icon: "trash3",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!confirmed) return;
    const resp = await fetch(`${baseUrl}/${hook.id}`, { method: "DELETE" });
    if (!resp.ok) {
      console.error("Failed to delete hook:", resp.status);
      return;
    }
    await loadHooks();
  };

  const runHook = async (hook: Hook) => {
    const resp = await fetch(`${baseUrl}/${hook.id}/run`, { method: "POST" });
    if (!resp.ok) {
      console.error("Failed to run hook:", resp.status);
      return;
    }
    await loadHooks();
  };

  const move = async (hook: Hook, delta: number) => {
    const phaseHooks = grouped[hook.phase];
    const idx = phaseHooks.findIndex((h) => h.id === hook.id);
    const target = idx + delta;
    if (idx < 0 || target < 0 || target >= phaseHooks.length) return;
    const reordered = phaseHooks.slice();
    const [taken] = reordered.splice(idx, 1);
    reordered.splice(target, 0, taken);
    const resp = await fetch(`${baseUrl}/reorder`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phase: hook.phase, hook_ids: reordered.map((h) => h.id) }),
    });
    if (!resp.ok) {
      console.error("Failed to reorder hooks:", resp.status);
      return;
    }
    await loadHooks();
  };

  const openEdit = (hook: Hook) => {
    setEditing({
      id: hook.id,
      name: hook.name,
      phase: hook.phase,
      command: hook.command,
      workdir: hook.workdir || "",
      envText: envObjectToText(hook.env),
      timeout_seconds: hook.timeout_seconds,
      enabled: hook.enabled,
      continue_on_failure: hook.continue_on_failure,
    });
    setEditorError("");
  };

  return (
    <div>
      <div class="d-flex justify-content-between align-items-start mb-3 flex-wrap gap-2">
        <div>
          <h5 class="mb-1">
            <i class="bi bi-lightning-charge me-1" />
            Boot Hooks
          </h5>
          <div class="text-muted small">
            Pre-create hooks run as ShoreGuard-side validation gates before the sandbox is
            created. Post-create hooks run inside the new sandbox via <code>ExecSandbox</code>{" "}
            once it is up.
          </div>
        </div>
        {isAdmin && (
          <button class="btn btn-primary btn-sm" onClick={() => setEditing(emptyEditing("pre_create"))}>
            <i class="bi bi-plus-lg me-1" />
            Add hook
          </button>
        )}
      </div>

      {loading && !loaded && <Spinner message="Loading hooks…" />}

      {loaded &&
        PHASES.map((phase) => (
          <div key={phase} class="card mb-3">
            <div class="card-header d-flex justify-content-between align-items-center">
              <strong>
                <i
                  class={`bi ${phase === "pre_create" ? "bi-shield-check" : "bi-arrow-right-circle"}`}
                />
                <span class="ms-1">{phaseLabel(phase)}</span>
              </strong>
              {isAdmin && (
                <button
                  class="btn btn-outline-primary btn-sm"
                  onClick={() => setEditing(emptyEditing(phase))}
                >
                  <i class="bi bi-plus-lg me-1" />
                  New
                </button>
              )}
            </div>
            <div class="card-body p-0">
              {grouped[phase].length === 0 && (
                <div class="text-muted small p-3">
                  No {phaseLabel(phase).toLowerCase()} hooks defined.
                </div>
              )}
              {grouped[phase].length > 0 && (
                <table class="table table-sm mb-0 align-middle">
                  <thead>
                    <tr class="text-muted small">
                      <th style="width:32px" />
                      <th style="width:36px">#</th>
                      <th>Name</th>
                      <th>Command</th>
                      <th style="width:90px">Timeout</th>
                      <th style="width:120px">Last run</th>
                      <th style="width:200px" class="text-end">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {grouped[phase].map((hook) => (
                      <>
                        <tr key={hook.id} class={!hook.enabled ? "text-muted" : ""}>
                          <td>
                            {isAdmin && (
                              <input
                                type="checkbox"
                                class="form-check-input"
                                checked={hook.enabled}
                                onChange={() => void toggleEnabled(hook)}
                              />
                            )}
                          </td>
                          <td class="text-muted small">{hook.order}</td>
                          <td>
                            <span class="font-monospace small">{hook.name}</span>
                            {hook.continue_on_failure && (
                              <span class="badge bg-warning text-dark ms-1" title="continue on failure">
                                soft
                              </span>
                            )}
                          </td>
                          <td>
                            <code
                              class="small text-truncate d-inline-block"
                              style="max-width: 360px;"
                              title={hook.command}
                            >
                              {hook.command}
                            </code>
                          </td>
                          <td class="small font-monospace">{hook.timeout_seconds}s</td>
                          <td class="small">
                            {hook.last_status ? (
                              <span
                                class={`badge ${hook.last_status === "success" ? "bg-success" : "bg-danger"}`}
                              >
                                {hook.last_status}
                              </span>
                            ) : (
                              <span class="text-muted">—</span>
                            )}
                          </td>
                          <td class="text-end">
                            {isOperator && (
                              <button
                                class="btn btn-sm btn-outline-secondary"
                                title="Run now"
                                onClick={() => void runHook(hook)}
                              >
                                <i class="bi bi-play" />
                              </button>
                            )}
                            {isAdmin && (
                              <>
                                <button
                                  class="btn btn-sm btn-outline-secondary"
                                  title="Edit"
                                  onClick={() => openEdit(hook)}
                                >
                                  <i class="bi bi-pencil" />
                                </button>
                                <button
                                  class="btn btn-sm btn-outline-secondary"
                                  title="Move up"
                                  disabled={hook.order === 0}
                                  onClick={() => void move(hook, -1)}
                                >
                                  <i class="bi bi-arrow-up" />
                                </button>
                                <button
                                  class="btn btn-sm btn-outline-secondary"
                                  title="Move down"
                                  disabled={hook.order === grouped[phase].length - 1}
                                  onClick={() => void move(hook, +1)}
                                >
                                  <i class="bi bi-arrow-down" />
                                </button>
                                <button
                                  class="btn btn-sm btn-outline-danger"
                                  title="Delete"
                                  onClick={() => void deleteHook(hook)}
                                >
                                  <i class="bi bi-trash3" />
                                </button>
                              </>
                            )}
                          </td>
                        </tr>
                        {hook.last_output && hook.last_status && (
                          <tr key={`out-${hook.id}`}>
                            <td colSpan={7} class="bg-light small">
                              <pre
                                class="mb-0"
                                style="white-space:pre-wrap; max-height:160px; overflow:auto;"
                              >
                                {hook.last_output}
                              </pre>
                            </td>
                          </tr>
                        )}
                      </>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        ))}

      {editing && (
        <Modal
          size="lg"
          onClose={() => setEditing(null)}
          title={<span>{editing.id ? "Edit hook" : "New hook"}</span>}
          footer={
            <button class="btn btn-primary btn-sm" onClick={() => void saveEditing()} disabled={saving}>
              {saving && <span class="spinner-border spinner-border-sm me-1" />}
              Save
            </button>
          }
        >
          <div class="row g-3">
            <div class="col-md-6">
              <label class="form-label small">Name</label>
              <input
                type="text"
                class="form-control form-control-sm"
                disabled={!!editing.id}
                value={editing.name}
                onInput={(e) => setEditing({ ...editing, name: (e.target as HTMLInputElement).value })}
              />
            </div>
            <div class="col-md-6">
              <label class="form-label small">Phase</label>
              <select
                class="form-select form-select-sm"
                disabled={!!editing.id}
                value={editing.phase}
                onChange={(e) =>
                  setEditing({ ...editing, phase: (e.target as HTMLSelectElement).value as Phase })
                }
              >
                <option value="pre_create">pre_create</option>
                <option value="post_create">post_create</option>
              </select>
            </div>
            <div class="col-12">
              <label class="form-label small">Command</label>
              <input
                type="text"
                class="form-control form-control-sm font-monospace"
                placeholder="e.g. apt update && apt install -y curl"
                value={editing.command}
                onInput={(e) =>
                  setEditing({ ...editing, command: (e.target as HTMLInputElement).value })
                }
              />
            </div>
            <div class="col-md-8">
              <label class="form-label small">Working directory (post-create only)</label>
              <input
                type="text"
                class="form-control form-control-sm font-monospace"
                placeholder="/workspace"
                value={editing.workdir}
                onInput={(e) =>
                  setEditing({ ...editing, workdir: (e.target as HTMLInputElement).value })
                }
              />
            </div>
            <div class="col-md-4">
              <label class="form-label small">Timeout (seconds)</label>
              <input
                type="number"
                class="form-control form-control-sm"
                min={1}
                max={600}
                value={editing.timeout_seconds}
                onInput={(e) =>
                  setEditing({
                    ...editing,
                    timeout_seconds: parseInt((e.target as HTMLInputElement).value, 10) || 30,
                  })
                }
              />
            </div>
            <div class="col-12">
              <label class="form-label small">Environment (KEY=VALUE per line)</label>
              <textarea
                class="form-control form-control-sm font-monospace"
                rows={3}
                placeholder={"FOO=bar\nTOKEN=secret"}
                value={editing.envText}
                onInput={(e) =>
                  setEditing({ ...editing, envText: (e.target as HTMLTextAreaElement).value })
                }
              />
            </div>
            <div class="col-md-6">
              <div class="form-check">
                <input
                  class="form-check-input"
                  type="checkbox"
                  id="hookEnabled"
                  checked={editing.enabled}
                  onChange={(e) =>
                    setEditing({ ...editing, enabled: (e.target as HTMLInputElement).checked })
                  }
                />
                <label class="form-check-label small" for="hookEnabled">
                  Enabled
                </label>
              </div>
            </div>
            <div class="col-md-6">
              <div class="form-check">
                <input
                  class="form-check-input"
                  type="checkbox"
                  id="hookSoft"
                  checked={editing.continue_on_failure}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      continue_on_failure: (e.target as HTMLInputElement).checked,
                    })
                  }
                />
                <label class="form-check-label small" for="hookSoft">
                  Continue on failure (post-create only)
                </label>
              </div>
            </div>
          </div>
          {editorError && <div class="alert alert-danger mt-3 small mb-0">{editorError}</div>}
        </Modal>
      )}
    </div>
  );
}
