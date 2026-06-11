/** New-sandbox wizard (island): agent type → config → policy → launch. */

import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { API, gwUrl, navigateTo } from "../lib/constants";
import { showToast } from "../lib/notify";
import { pollOperation } from "../lib/operations";

interface CommunitySandbox {
  name: string;
  description?: string;
  image?: string;
  default_provider?: string;
}

interface SandboxTemplate {
  name: string;
  description?: string;
  category?: string;
}

interface Provider {
  name: string;
  type: string;
}

interface Preset {
  name: string;
  description?: string;
}

interface EnvRow {
  id: number;
  key: string;
  val: string;
}

interface LogLine {
  text: string;
  cls?: string;
}

const STEP_LABELS = ["Agent Type", "Configuration", "Policy", "Launch"];
const CATEGORY_ICONS: Record<string, string> = {
  ml: "gpu-card",
  dev: "code-slash",
  security: "shield-lock",
};

export default function WizardPage() {
  const [step, setStep] = useState(1);
  const [agent, setAgent] = useState<string | null>(null);
  const [fromTemplate, setFromTemplate] = useState(false);
  const [templateProviders, setTemplateProviders] = useState<string[]>([]);
  const [defaultProvider, setDefaultProvider] = useState("");

  const [community, setCommunity] = useState<CommunitySandbox[]>([]);
  const [templates, setTemplates] = useState<SandboxTemplate[]>([]);
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [presetsError, setPresetsError] = useState(false);

  const [name, setName] = useState("");
  const [image, setImage] = useState("");
  const [gpu, setGpu] = useState(false);
  const [logLevel, setLogLevel] = useState("");
  const [description, setDescription] = useState("");
  const [envRows, setEnvRows] = useState<EnvRow[]>([]);
  const envCounter = useRef(0);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [labelKey, setLabelKey] = useState("");
  const [labelVal, setLabelVal] = useState("");
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [selectedPresets, setSelectedPresets] = useState<Set<string>>(new Set());

  const [launching, setLaunching] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressState, setProgressState] = useState<"running" | "success" | "error" | null>(null);
  const [log, setLog] = useState<LogLine[]>([]);
  const [resultName, setResultName] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiFetch<CommunitySandbox[]>(`${API}/providers/community-sandboxes`)
      .then(setCommunity)
      .catch(() => undefined);
    apiFetch<SandboxTemplate[]>(`/api/sandbox-templates`)
      .then(setTemplates)
      .catch(() => undefined);
  }, []);

  const loadProviders = async () => {
    if (providers !== null) return;
    try {
      const resp = await apiFetch<Provider[] | { items?: Provider[] }>(`${API}/providers`);
      setProviders(Array.isArray(resp) ? resp : (resp.items ?? []));
    } catch {
      setProviders([]);
    }
  };

  const loadPresets = async () => {
    if (presets !== null) return;
    try {
      setPresets(await apiFetch<Preset[]>(`/api/policies/presets`));
    } catch {
      setPresets([]);
      setPresetsError(true);
    }
  };

  const goTo = (next: number) => {
    setStep(next);
    if (next === 2) void loadProviders();
    if (next === 3) void loadPresets();
  };

  const selectAgent = (type: string) => {
    setAgent(type);
    setFromTemplate(false);
    const sandbox = community.find((s) => s.name === type);
    setImage(sandbox?.image ?? "");
    const def = sandbox?.default_provider ?? "";
    setDefaultProvider(def);
    if (def) setSelectedProviders(new Set([def]));
    setTimeout(() => goTo(2), 200);
  };

  const selectTemplate = async (templateName: string) => {
    try {
      const tpl = await apiFetch<{ sandbox?: Record<string, unknown> }>(
        `/api/sandbox-templates/${templateName}`,
      );
      const sb = tpl.sandbox ?? {};
      setImage((sb.image as string) || "");
      setGpu(Boolean(sb.gpu));
      const env = (sb.environment as Record<string, string>) ?? {};
      setEnvRows(
        Object.entries(env).map(([key, val]) => ({ id: envCounter.current++, key, val })),
      );
      setDescription((sb.description as string) || "");
      setLabels({ ...((sb.labels as Record<string, string>) ?? {}) });
      setSelectedPresets(new Set((sb.presets as string[]) ?? []));
      setAgent(templateName);
      setDefaultProvider("");
      setFromTemplate(true);
      setTemplateProviders((sb.providers as string[]) ?? []);
      goTo(4);
    } catch (e) {
      showToast(`Failed to load template: ${(e as Error).message}`, "danger");
    }
  };

  const addLabel = () => {
    const k = labelKey.trim();
    const v = labelVal.trim();
    if (!k || labels[k] !== undefined) return;
    if (Object.keys(labels).length >= 20) return;
    setLabels({ ...labels, [k]: v });
    setLabelKey("");
    setLabelVal("");
  };

  const effectiveProviders =
    fromTemplate && templateProviders.length > 0 ? templateProviders : [...selectedProviders];

  const envVars = Object.fromEntries(
    envRows.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.val]),
  );

  const addLog = (text: string, cls = "") => {
    setLog((prev) => [...prev, { text, cls }]);
    requestAnimationFrame(() => {
      const el = logRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  };

  const launch = async () => {
    setLaunching(true);
    setProgressState("running");
    setProgress(20);
    setLog([]);
    setResultName(null);
    const presetList = [...selectedPresets];
    try {
      addLog("Creating sandbox...");
      if (presetList.length > 0) addLog(`Presets: ${presetList.join(", ")}`);

      const response = await apiFetch<{ operation_id: string }>(`${API}/sandboxes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          image,
          gpu,
          providers: effectiveProviders.length > 0 ? effectiveProviders : undefined,
          environment: Object.keys(envVars).length > 0 ? envVars : undefined,
          presets: presetList.length > 0 ? presetList : undefined,
          description: description.trim() || undefined,
          labels: Object.keys(labels).length > 0 ? labels : undefined,
          log_level: logLevel || undefined,
        }),
      });

      addLog("Sandbox submitted, waiting for ready state...");

      const op = await pollOperation(response.operation_id, {
        onProgress: (pct, msg) => {
          setProgress(Math.max(20, pct));
          if (msg) addLog(msg);
        },
      });

      if (op.status === "failed") {
        throw new Error(op.error || "Sandbox creation failed");
      }

      const sandbox = op.result as {
        name: string;
        presets_applied?: string[];
        presets_failed?: { preset: string; error: string }[];
        preset_error?: string;
        preset_warning?: string;
      };
      addLog(`Sandbox "${sandbox.name}" created.`, "log-info");
      if ((sandbox.presets_applied ?? []).length > 0) {
        addLog(`Presets applied: ${sandbox.presets_applied!.join(", ")}`, "log-info");
      }
      for (const f of sandbox.presets_failed ?? []) {
        addLog(`Warning: preset "${f.preset}" failed: ${f.error}`, "log-warn");
      }
      if (sandbox.preset_error) addLog(`Warning: ${sandbox.preset_error}`, "log-warn");
      if (sandbox.preset_warning) addLog(`Warning: ${sandbox.preset_warning}`, "log-warn");

      setProgress(100);
      setProgressState("success");
      setResultName(sandbox.name);
    } catch (e) {
      addLog(`Error: ${(e as Error).message}`, "log-error");
      setProgressState("error");
    }
  };

  const stepIndicator = (
    <div class="d-flex mb-4">
      {STEP_LABELS.map((label, i) => {
        const n = i + 1;
        return (
          <div
            key={n}
            class={`wizard-step ${n === step ? "active" : ""} ${n < step ? "completed" : ""}`}
          >
            <span class={`badge rounded-pill ${n < step ? "bg-success" : n === step ? "bg-primary" : "bg-secondary"} me-2`}>
              {n}
            </span>
            {label}
          </div>
        );
      })}
    </div>
  );

  return (
    <div class="row justify-content-center">
      <div class="col-lg-10 col-xl-8">
        <h4 class="mb-4">Create New Sandbox</h4>
        {stepIndicator}

        {step === 1 && (
          <div class="wizard-content">
            {templates.length > 0 && (
              <div class="row g-3 mb-4">
                {templates.map((t) => (
                  <div key={t.name} class="col">
                    <div
                      class="card sg-card-themed h-100 agent-card sg-cursor-pointer"
                      onClick={() => void selectTemplate(t.name)}
                    >
                      <div class="card-body text-center py-3">
                        <i
                          class={`bi bi-${CATEGORY_ICONS[t.category ?? ""] || "file-earmark-code"} fs-3 d-block mb-2 sg-text-accent`}
                        />
                        <div class="fw-semibold">{t.name}</div>
                        <div class="text-muted small mt-1">{t.description}</div>
                        <span class="badge text-bg-secondary mt-2">{t.category || "general"}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div class="row g-3">
              {community.map((sandbox) => (
                <div key={sandbox.name} class="col-md-6">
                  <div
                    class={`card agent-card ${agent === sandbox.name ? "selected" : ""}`}
                    onClick={() => selectAgent(sandbox.name)}
                  >
                    <div class="card-body text-center py-4">
                      <i class="bi bi-robot fs-1 text-success" />
                      <h5 class="mt-3">{sandbox.name}</h5>
                      <p class="text-muted small">{sandbox.description}</p>
                    </div>
                  </div>
                </div>
              ))}
              <div class="col-md-6">
                <div
                  class={`card agent-card ${agent === "custom" ? "selected" : ""}`}
                  onClick={() => selectAgent("custom")}
                >
                  <div class="card-body text-center py-4">
                    <i class="bi bi-gear fs-1 text-secondary" />
                    <h5 class="mt-3">Custom</h5>
                    <p class="text-muted small">Configure manually</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {step === 2 && (
          <div class="wizard-content">
            <div class="card">
              <div class="card-body">
                <div class="mb-3">
                  <label class="form-label">Sandbox Name</label>
                  <input
                    type="text"
                    class="form-control"
                    placeholder="my-sandbox"
                    value={name}
                    onInput={(e) => setName((e.target as HTMLInputElement).value)}
                  />
                </div>
                <div class="mb-3">
                  <label class="form-label">Container Image</label>
                  <input
                    type="text"
                    class="form-control"
                    placeholder="(default base image)"
                    value={image}
                    onInput={(e) => setImage((e.target as HTMLInputElement).value)}
                  />
                  <div class="form-text">Leave blank to use the gateway's default base image.</div>
                </div>
                <div class="mb-3">
                  <div class="form-check form-switch">
                    <input
                      class="form-check-input"
                      type="checkbox"
                      id="wizard-gpu"
                      checked={gpu}
                      onChange={(e) => setGpu((e.target as HTMLInputElement).checked)}
                    />
                    <label class="form-check-label" for="wizard-gpu">
                      Request GPU resources
                    </label>
                  </div>
                </div>
                <div class="mb-3">
                  <label class="form-label" for="wizard-log-level">
                    Supervisor Log Level <span class="text-muted small">(optional)</span>
                  </label>
                  <select
                    id="wizard-log-level"
                    class="form-select"
                    value={logLevel}
                    onChange={(e) => setLogLevel((e.target as HTMLSelectElement).value)}
                  >
                    <option value="">Gateway default</option>
                    <option value="debug">debug</option>
                    <option value="info">info</option>
                    <option value="warn">warn</option>
                    <option value="error">error</option>
                  </select>
                  <div class="form-text">
                    Controls how verbose the sandbox supervisor's own logs are. Leave blank to
                    inherit the gateway's default.
                  </div>
                </div>
                <div class="mb-3">
                  <label class="form-label">
                    Providers <span class="text-muted small">(optional)</span>
                  </label>
                  {providers === null && (
                    <div class="text-muted small">
                      <div class="spinner-border spinner-border-sm me-1" />
                    </div>
                  )}
                  {providers !== null && providers.length === 0 && (
                    <div class="text-muted small py-1">
                      <i class="bi bi-info-circle me-1" />
                      No providers configured yet. Sandboxes auto-create providers on demand.
                    </div>
                  )}
                  {providers !== null &&
                    providers.map((p) => {
                      const checked =
                        selectedProviders.has(p.name) ||
                        defaultProvider === p.name ||
                        defaultProvider === p.type;
                      return (
                        <div key={p.name} class="form-check">
                          <input
                            class="form-check-input"
                            type="checkbox"
                            id={`wiz-prov-${p.name}`}
                            checked={checked}
                            onChange={(e) => {
                              const next = new Set(selectedProviders);
                              if ((e.target as HTMLInputElement).checked) {
                                next.add(p.name);
                              } else {
                                next.delete(p.name);
                              }
                              if (defaultProvider === p.name || defaultProvider === p.type) {
                                setDefaultProvider("");
                              }
                              setSelectedProviders(next);
                            }}
                          />
                          <label class="form-check-label" for={`wiz-prov-${p.name}`}>
                            <strong>{p.name}</strong>
                            <span class="badge text-bg-secondary ms-1">{p.type}</span>
                          </label>
                        </div>
                      );
                    })}
                  <div class="form-text">
                    Select providers to attach. Sandboxes can also auto-create providers on demand.
                  </div>
                </div>
                <div class="mb-3">
                  <label class="form-label">
                    Environment Variables <span class="text-muted small">(optional)</span>
                  </label>
                  {envRows.map((row) => (
                    <div key={row.id} class="input-group input-group-sm mb-1">
                      <input
                        type="text"
                        class="form-control"
                        placeholder="KEY"
                        value={row.key}
                        onInput={(e) =>
                          setEnvRows(
                            envRows.map((r) =>
                              r.id === row.id
                                ? { ...r, key: (e.target as HTMLInputElement).value }
                                : r,
                            ),
                          )
                        }
                      />
                      <span class="input-group-text">=</span>
                      <input
                        type="text"
                        class="form-control"
                        placeholder="value"
                        value={row.val}
                        onInput={(e) =>
                          setEnvRows(
                            envRows.map((r) =>
                              r.id === row.id
                                ? { ...r, val: (e.target as HTMLInputElement).value }
                                : r,
                            ),
                          )
                        }
                      />
                      <button
                        class="btn btn-outline-danger"
                        type="button"
                        onClick={() => setEnvRows(envRows.filter((r) => r.id !== row.id))}
                      >
                        <i class="bi bi-x" />
                      </button>
                    </div>
                  ))}
                  <button
                    class="btn btn-outline-secondary btn-sm mt-1"
                    type="button"
                    onClick={() =>
                      setEnvRows([...envRows, { id: envCounter.current++, key: "", val: "" }])
                    }
                  >
                    <i class="bi bi-plus me-1" />
                    Add Variable
                  </button>
                </div>
                <div class="mb-3">
                  <label class="form-label">
                    Description <span class="text-muted small">(optional)</span>
                  </label>
                  <input
                    type="text"
                    class="form-control"
                    placeholder="e.g. ML training sandbox for team-alpha"
                    maxLength={1000}
                    value={description}
                    onInput={(e) => setDescription((e.target as HTMLInputElement).value)}
                  />
                </div>
                <div class="mb-3">
                  <label class="form-label">
                    Labels <span class="text-muted small">(optional)</span>
                  </label>
                  {Object.keys(labels).length > 0 && (
                    <div class="mb-1 d-flex flex-wrap gap-1">
                      {Object.entries(labels).map(([k, v]) => (
                        <span
                          key={k}
                          class="badge text-bg-light border d-inline-flex align-items-center gap-1"
                        >
                          <span class="font-monospace">{k}</span>
                          <span class="text-muted">=</span>
                          <span>{v}</span>
                          <button
                            type="button"
                            class="btn-close btn-close-sm ms-1 sg-fs-xxs"
                            onClick={() => {
                              const { [k]: _removed, ...rest } = labels;
                              setLabels(rest);
                            }}
                          />
                        </span>
                      ))}
                    </div>
                  )}
                  {Object.keys(labels).length < 20 && (
                    <div class="input-group input-group-sm sg-mw-400">
                      <input
                        type="text"
                        class="form-control font-monospace"
                        placeholder="key"
                        value={labelKey}
                        onInput={(e) => setLabelKey((e.target as HTMLInputElement).value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            addLabel();
                          }
                        }}
                      />
                      <span class="input-group-text">=</span>
                      <input
                        type="text"
                        class="form-control"
                        placeholder="value"
                        value={labelVal}
                        onInput={(e) => setLabelVal((e.target as HTMLInputElement).value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            addLabel();
                          }
                        }}
                      />
                      <button class="btn btn-outline-success" type="button" onClick={addLabel}>
                        <i class="bi bi-plus" />
                      </button>
                    </div>
                  )}
                </div>
                <div class="d-flex justify-content-between">
                  <button class="btn btn-outline-secondary" onClick={() => goTo(1)}>
                    Back
                  </button>
                  <button class="btn btn-primary" onClick={() => goTo(3)}>
                    Next
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {step === 3 && (
          <div class="wizard-content">
            <p class="text-muted mb-3">Select policy presets to enable:</p>
            <div class="mb-3">
              {presets === null && (
                <div class="text-center text-muted py-3">
                  <div class="spinner-border spinner-border-sm me-2" />
                  Loading presets...
                </div>
              )}
              {presets !== null && presetsError && (
                <p class="text-warning">
                  <i class="bi bi-exclamation-triangle me-1" />
                  Could not load presets. The sandbox will start with the default policy.
                </p>
              )}
              {presets !== null && !presetsError && presets.length === 0 && (
                <p class="text-muted">
                  <i class="bi bi-info-circle me-1" />
                  No policy presets available. The sandbox will start with the default policy.
                </p>
              )}
              {presets !== null && presets.length > 0 && (
                <table class="table table-sm table-hover align-middle mb-0">
                  <thead>
                    <tr>
                      <th class="sg-w-40" />
                      <th>Preset</th>
                      <th>Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {presets.map((p) => (
                      <tr
                        key={p.name}
                        class="table-clickable"
                        onClick={() => {
                          const next = new Set(selectedPresets);
                          if (next.has(p.name)) {
                            next.delete(p.name);
                          } else {
                            next.add(p.name);
                          }
                          setSelectedPresets(next);
                        }}
                      >
                        <td>
                          <input
                            class="form-check-input"
                            type="checkbox"
                            checked={selectedPresets.has(p.name)}
                            onClick={(e) => e.stopPropagation()}
                            onChange={() => {
                              const next = new Set(selectedPresets);
                              if (next.has(p.name)) {
                                next.delete(p.name);
                              } else {
                                next.add(p.name);
                              }
                              setSelectedPresets(next);
                            }}
                          />
                        </td>
                        <td>
                          <strong>{p.name}</strong>
                        </td>
                        <td class="text-muted small">{p.description || ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div class="d-flex justify-content-between">
              <button class="btn btn-outline-secondary" onClick={() => goTo(2)}>
                Back
              </button>
              <button class="btn btn-primary" onClick={() => goTo(4)}>
                Next
              </button>
            </div>
          </div>
        )}

        {step === 4 && (
          <div class="wizard-content">
            <div class="card">
              <div class="card-body">
                <h5>Summary</h5>
                <dl class="row mb-3">
                  <dt class="col-sm-3 text-muted">Agent</dt>
                  <dd class="col-sm-9">{agent || "custom"}</dd>
                  <dt class="col-sm-3 text-muted">Name</dt>
                  <dd class="col-sm-9">{name || "(auto-generated)"}</dd>
                  <dt class="col-sm-3 text-muted">Image</dt>
                  <dd class="col-sm-9 font-monospace small">{image || "(default)"}</dd>
                  <dt class="col-sm-3 text-muted">GPU</dt>
                  <dd class="col-sm-9">
                    {gpu ? (
                      <span>
                        <i class="bi bi-check-circle text-success" /> Yes
                      </span>
                    ) : (
                      "No"
                    )}
                  </dd>
                  {effectiveProviders.length > 0 && (
                    <>
                      <dt class="col-sm-3 text-muted">Providers</dt>
                      <dd class="col-sm-9">
                        {effectiveProviders.map((p) => (
                          <span key={p} class="badge text-bg-info me-1">
                            {p}
                          </span>
                        ))}
                      </dd>
                    </>
                  )}
                  {Object.keys(envVars).length > 0 && (
                    <>
                      <dt class="col-sm-3 text-muted">Env Vars</dt>
                      <dd class="col-sm-9">
                        {Object.entries(envVars).map(([k, v]) => (
                          <span key={k} class="badge text-bg-secondary me-1 font-monospace">
                            {k}={v}
                          </span>
                        ))}
                      </dd>
                    </>
                  )}
                  {description && (
                    <>
                      <dt class="col-sm-3 text-muted">Description</dt>
                      <dd class="col-sm-9">{description}</dd>
                    </>
                  )}
                  {Object.keys(labels).length > 0 && (
                    <>
                      <dt class="col-sm-3 text-muted">Labels</dt>
                      <dd class="col-sm-9">
                        {Object.entries(labels).map(([k, v]) => (
                          <span key={k} class="badge text-bg-light border font-monospace me-1">
                            {k}={v}
                          </span>
                        ))}
                      </dd>
                    </>
                  )}
                  <dt class="col-sm-3 text-muted">Presets</dt>
                  <dd class="col-sm-9">
                    {selectedPresets.size > 0 ? (
                      [...selectedPresets].map((p) => (
                        <span key={p} class="badge text-bg-secondary me-1">
                          {p}
                        </span>
                      ))
                    ) : (
                      <span class="text-muted">None</span>
                    )}
                  </dd>
                </dl>

                {progressState !== null && (
                  <div class="mb-3">
                    <div class="progress mb-2">
                      <div
                        class={`progress-bar progress-bar-striped ${
                          progressState === "running" ? "progress-bar-animated" : ""
                        } ${progressState === "success" ? "bg-success" : ""} ${
                          progressState === "error" ? "bg-danger" : ""
                        }`}
                        style={`width: ${progress}%`}
                      />
                    </div>
                    <div ref={logRef} class="log-output small">
                      {log.map((line, i) => (
                        <div key={i} class={`log-line ${line.cls || ""}`}>
                          {line.text}
                        </div>
                      ))}
                      {resultName && (
                        <div class="mt-3 pt-3 border-top border-secondary">
                          <div class="d-flex align-items-center mb-3">
                            <i class="bi bi-check-circle-fill text-success fs-4 me-2" />
                            <strong>Sandbox "{resultName}" is running.</strong>
                          </div>
                          <div class="d-flex gap-2">
                            <button
                              class="btn btn-success btn-sm"
                              onClick={() => navigateTo(gwUrl(`/sandboxes/${resultName}`))}
                            >
                              <i class="bi bi-box-arrow-in-right me-1" />
                              Open Sandbox
                            </button>
                            <button
                              class="btn btn-outline-light btn-sm"
                              onClick={() => navigateTo(gwUrl("/sandboxes"))}
                            >
                              <i class="bi bi-grid me-1" />
                              Sandboxes
                            </button>
                            <button
                              class="btn btn-outline-light btn-sm"
                              onClick={() => navigateTo(gwUrl("/wizard"))}
                            >
                              <i class="bi bi-plus-circle me-1" />
                              Create Another
                            </button>
                          </div>
                        </div>
                      )}
                      {progressState === "error" && (
                        <div class="mt-3 pt-3 border-top border-secondary">
                          <div class="d-flex gap-2">
                            <button
                              class="btn btn-outline-light btn-sm"
                              onClick={() => navigateTo(gwUrl("/wizard"))}
                            >
                              <i class="bi bi-arrow-clockwise me-1" />
                              Try Again
                            </button>
                            <button
                              class="btn btn-outline-light btn-sm"
                              onClick={() => navigateTo(gwUrl("/sandboxes"))}
                            >
                              <i class="bi bi-grid me-1" />
                              Sandboxes
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <div class="d-flex justify-content-between">
                  <div class="d-flex gap-2">
                    <button
                      class="btn btn-outline-secondary"
                      disabled={launching}
                      onClick={() => goTo(3)}
                    >
                      Back
                    </button>
                    <button
                      class="btn btn-outline-secondary"
                      disabled={launching}
                      onClick={() => goTo(2)}
                    >
                      <i class="bi bi-pencil me-1" />
                      Customize
                    </button>
                  </div>
                  <button
                    class="btn btn-success"
                    disabled={launching}
                    onClick={() => void launch()}
                  >
                    <i class="bi bi-rocket me-1" />
                    Launch Sandbox
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
