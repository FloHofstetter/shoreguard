/** Z3 policy verification page (island). */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { API, GW } from "../lib/constants";

interface Preset {
  query_id: string;
  label: string;
  description?: string;
  params?: Record<string, { placeholder?: string }>;
}

interface SelectedQuery {
  query_id: string;
  label: string;
  description?: string;
  paramDefs: Record<string, { placeholder?: string }>;
  params: Record<string, string>;
}

interface VerifyResult {
  verdict: string;
  query: string;
  z3_time_ms: number;
  counterexample?: Record<string, string> | null;
}

function verdictIcon(verdict: string): string {
  if (verdict === "SAFE") return "bi-check-circle-fill text-success";
  if (verdict === "VULNERABLE") return "bi-exclamation-triangle-fill text-danger";
  if (verdict === "TIMEOUT") return "bi-hourglass-split text-warning";
  return "bi-x-circle text-secondary";
}

function verdictColor(verdict: string): string {
  if (verdict === "SAFE") return "text-success";
  if (verdict === "VULNERABLE") return "text-danger";
  if (verdict === "TIMEOUT") return "text-warning";
  return "text-secondary";
}

function verdictBorder(verdict: string): string {
  if (verdict === "SAFE") return "border-success";
  if (verdict === "VULNERABLE") return "border-danger";
  if (verdict === "TIMEOUT") return "border-warning";
  return "";
}

interface SimResult {
  host?: string;
  port?: number;
  method?: string;
  path?: string;
  predicted_decision: string;
}

interface SimResponse {
  results: SimResult[];
  summary: { total: number; would_allow: number; still_deny: number };
  best_effort: boolean;
}

/** Denial-replay simulation: replays the persisted denial corpus against the
 *  current active policy and predicts which previously-denied requests it now
 *  allows. Uses apiFetch (not raw fetch) so the /policy/simulate route is
 *  covered by the surface-coverage check. */
function DenialReplayPanel({ name }: { name: string }) {
  const [sim, setSim] = useState<SimResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setRunning(true);
    setError("");
    setSim(null);
    try {
      const data = await apiFetch<SimResponse>(
        `${API}/sandboxes/${encodeURIComponent(name)}/policy/simulate`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
      );
      setSim(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div class="card sg-card-themed mb-4">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <h6 class="mb-0">
            <i class="bi bi-arrow-repeat me-2" />
            Denial replay (best-effort)
          </h6>
          <button class="btn btn-sm btn-outline-primary" disabled={running} onClick={() => void run()}>
            {running ? "Replaying…" : "Replay denials"}
          </button>
        </div>
        <p class="small text-muted mb-2">
          Replays the recorded denial corpus against the active policy and predicts which
          previously-blocked requests it would now allow. Best-effort — deterministic evaluation
          may diverge from the live gateway matcher.
        </p>
        {error && <div class="alert alert-warning py-2 small mb-0">{error}</div>}
        {sim && (
          <div>
            <div class="small mb-2">
              <span class="badge text-bg-success me-1">{sim.summary.would_allow} would allow</span>
              <span class="badge text-bg-secondary me-1">{sim.summary.still_deny} still deny</span>
              <span class="text-muted">of {sim.summary.total} sampled requests</span>
            </div>
            <div class="table-responsive">
              <table class="table table-sm align-middle mb-0">
                <tbody>
                  {sim.results.slice(0, 25).map((r, i) => (
                    <tr key={i}>
                      <td>
                        <span
                          class={`badge ${r.predicted_decision === "allow" ? "text-bg-success" : "text-bg-secondary"}`}
                        >
                          {r.predicted_decision}
                        </span>
                      </td>
                      <td class="font-monospace small">
                        {r.method} {r.host}
                        {r.port ? `:${r.port}` : ""}
                        {r.path}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function SandboxProverPage({ name }: { name: string }) {
  const base = `/api/gateways/${encodeURIComponent(GW)}/sandboxes/${encodeURIComponent(name)}`;
  const [presets, setPresets] = useState<Preset[]>([]);
  const [selectedQueries, setSelectedQueries] = useState<SelectedQuery[]>([]);
  const [results, setResults] = useState<VerifyResult[]>([]);
  const [totalTimeMs, setTotalTimeMs] = useState(0);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${base}/policy/verify/presets`)
      .then((res) => (res.ok ? res.json() : []))
      .then(setPresets)
      .catch((e) => console.error("Failed to load presets:", e));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const addQuery = (preset: Preset) => {
    const paramDefs = preset.params ?? {};
    const params = Object.fromEntries(Object.keys(paramDefs).map((p) => [p, ""]));
    setSelectedQueries([
      ...selectedQueries,
      {
        query_id: preset.query_id,
        label: preset.label,
        description: preset.description,
        paramDefs,
        params,
      },
    ]);
  };

  const removeQuery = (idx: number) => {
    setSelectedQueries(selectedQueries.filter((_, i) => i !== idx));
  };

  const setParam = (idx: number, pname: string, value: string) => {
    setSelectedQueries(
      selectedQueries.map((sq, i) =>
        i === idx ? { ...sq, params: { ...sq.params, [pname]: value } } : sq,
      ),
    );
  };

  const runVerification = async () => {
    setRunning(true);
    setError("");
    setResults([]);
    try {
      const queries = selectedQueries.map((sq) => ({ query_id: sq.query_id, params: sq.params }));
      const res = await fetch(`${base}/policy/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ queries }),
      });
      if (res.ok) {
        const data = await res.json();
        setResults(data.results ?? []);
        setTotalTimeMs(data.total_time_ms ?? 0);
      } else {
        const err = await res.json().catch(() => ({}));
        setError(err.detail || `Verification failed (${res.status})`);
      }
    } catch (e) {
      setError(`Network error: ${(e as Error).message}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <div>
          <h5 class="mb-1">Policy Formal Verification</h5>
          <p class="text-muted small mb-0">
            Run Z3 SMT solver queries against the active policy to prove security properties.
          </p>
        </div>
      </div>

      <DenialReplayPanel name={name} />

      <div class="card mb-3">
        <div class="card-header d-flex justify-content-between align-items-center">
          <span class="fw-medium">Verification Queries</span>
          <div class="dropdown">
            <button class="btn btn-sm btn-primary dropdown-toggle" data-bs-toggle="dropdown">
              <i class="bi bi-plus-lg me-1" />
              Add Query
            </button>
            <ul class="dropdown-menu">
              {presets.map((preset) => (
                <li key={preset.query_id}>
                  <a
                    class="dropdown-item"
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      addQuery(preset);
                    }}
                  >
                    <div class="fw-medium">{preset.label}</div>
                    <div class="text-muted small">{preset.description}</div>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </div>
        <div class="card-body">
          {selectedQueries.length === 0 && (
            <div class="text-center text-muted py-4">
              <i class="bi bi-patch-question fs-1" />
              <p class="mt-2">Select a verification query from the dropdown above.</p>
            </div>
          )}

          {selectedQueries.map((sq, idx) => (
            <div key={idx} class="border rounded p-3 mb-2">
              <div class="d-flex justify-content-between align-items-start mb-2">
                <div>
                  <span class="fw-medium">{sq.label}</span>
                  <span class="text-muted small ms-2">{sq.description}</span>
                </div>
                <button
                  class="btn btn-sm btn-outline-danger"
                  title="Remove"
                  onClick={() => removeQuery(idx)}
                >
                  <i class="bi bi-x-lg" />
                </button>
              </div>
              {Object.entries(sq.paramDefs).map(([pname, pdef]) => (
                <div key={pname} class="mb-2">
                  <label class="form-label small mb-1">{pname}</label>
                  <input
                    type="text"
                    class="form-control form-control-sm"
                    placeholder={pdef.placeholder || ""}
                    value={sq.params[pname]}
                    onInput={(e) => setParam(idx, pname, (e.target as HTMLInputElement).value)}
                  />
                </div>
              ))}
            </div>
          ))}

          {selectedQueries.length > 0 && (
            <div class="mt-3">
              <button class="btn btn-primary" onClick={() => void runVerification()} disabled={running}>
                {running ? (
                  <span>
                    <span class="spinner-border spinner-border-sm me-1" />
                    Verifying...
                  </span>
                ) : (
                  <span>
                    <i class="bi bi-play-fill me-1" />
                    Run Verification
                  </span>
                )}
              </button>
            </div>
          )}
        </div>
      </div>

      {results.length > 0 && (
        <div>
          <div class="d-flex justify-content-between align-items-center mb-2">
            <h6 class="mb-0">Results</h6>
            <span class="text-muted small">
              Total: <span>{totalTimeMs.toFixed(1)}</span> ms
            </span>
          </div>
          {results.map((r, i) => (
            <div key={i} class={`card mb-2 ${verdictBorder(r.verdict)}`}>
              <div class="card-body py-2">
                <div class="d-flex justify-content-between align-items-center">
                  <div class="d-flex align-items-center gap-2">
                    <i class={`bi fs-5 ${verdictIcon(r.verdict)}`} />
                    <div>
                      <span class={`fw-medium ${verdictColor(r.verdict)}`}>{r.verdict}</span>
                      <span class="text-muted small ms-2">{r.query}</span>
                    </div>
                  </div>
                  <span class="badge bg-light text-dark">{r.z3_time_ms.toFixed(1)} ms</span>
                </div>
                {r.counterexample && (
                  <div class="mt-2">
                    <div class="bg-light rounded p-2 font-monospace small">
                      <div class="text-danger fw-medium mb-1">Counterexample:</div>
                      {Object.entries(r.counterexample).map(([k, v]) => (
                        <div key={k}>
                          <span class="text-muted">{k}: </span>
                          <span>{v}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {error && <div class="alert alert-danger">{error}</div>}
    </div>
  );
}
