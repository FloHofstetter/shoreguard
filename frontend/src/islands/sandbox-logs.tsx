/** Sandbox log viewer (island): level toggles, OCSF filters, text filter. */

import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { API, badgeClass, CONFIG, GW } from "../lib/constants";
import { ErrorAlert, Spinner } from "../lib/widgets";

interface OcsfInfo {
  class_prefix?: string;
  activity?: string;
  severity?: string;
  disposition?: string;
  summary?: string;
  binary?: string;
  bracket_fields?: Record<string, string>;
  fields?: Record<string, string>;
}

interface LogEntry {
  timestamp_ms?: number;
  level?: string;
  source?: string;
  message?: string;
  ocsf?: OcsfInfo | null;
}

function formatTimestamp(ms: number | undefined): string {
  return ms ? new Date(ms).toLocaleString() : "";
}

function logCss(log: LogEntry): string {
  if (log.ocsf) {
    const disp = (log.ocsf.disposition || "").toLowerCase();
    const cls = ["log-line", "log-ocsf"];
    if (disp) cls.push(`log-ocsf-${disp}`);
    return cls.join(" ");
  }
  return `log-line log-${(log.level || "info").toLowerCase()}`;
}

function isDeniedOcsf(log: LogEntry): boolean {
  const d = (log.ocsf?.disposition || "").toUpperCase();
  return d === "DENIED" || d === "BLOCKED";
}

function hasExpandable(log: LogEntry): boolean {
  if (!log.ocsf) return false;
  return (
    Object.keys(log.ocsf.bracket_fields ?? {}).length > 0 ||
    Object.keys(log.ocsf.fields ?? {}).length > 0
  );
}

function FieldsList({ fields }: { fields: Record<string, string> }) {
  return (
    <dl class="row mb-1">
      {Object.entries(fields).map(([k, v]) => (
        <div key={k} class="d-flex">
          <dt class="log-ocsf-key">{k}:</dt>
          <dd class="log-ocsf-value mb-0 ms-1">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function SandboxLogsPage({ name }: { name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [allLogs, setAllLogs] = useState<LogEntry[]>([]);
  const [showInfo, setShowInfo] = useState(true);
  const [showWarn, setShowWarn] = useState(true);
  const [showError, setShowError] = useState(true);
  const [showOcsf, setShowOcsf] = useState(true);
  const [serverOcsfOnly, setServerOcsfOnly] = useState(false);
  const [classFilter, setClassFilter] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [filterText, setFilterText] = useState(
    () => new URLSearchParams(window.location.search).get("text") || "",
  );
  const containerRef = useRef<HTMLDivElement>(null);

  const load = async (ocsfOnly = serverOcsfOnly) => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ lines: String(CONFIG.logLinesDefault) });
      if (ocsfOnly) params.set("ocsf_only", "true");
      const logs = await apiFetch<LogEntry[]>(`${API}/sandboxes/${name}/logs?${params}`);
      setAllLogs(logs);
      setClassFilter((prev) => {
        const next = { ...prev };
        for (const log of logs) {
          const cls = log.ocsf?.class_prefix;
          if (cls && next[cls] === undefined) next[cls] = true;
        }
        return next;
      });
      requestAnimationFrame(() => {
        const el = containerRef.current;
        if (el) el.scrollTop = el.scrollHeight;
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const availableClasses = Array.from(
    new Set(
      allLogs
        .map((log) => log.ocsf?.class_prefix)
        .filter((cls): cls is string => Boolean(cls)),
    ),
  ).sort();

  const needle = filterText.toLowerCase();
  const filteredLogs = allLogs.filter((log) => {
    if (log.ocsf) {
      if (!showOcsf) return false;
      const cls = log.ocsf.class_prefix;
      if (cls && classFilter[cls] === false) return false;
    } else {
      const level = (log.level || "info").toLowerCase();
      if (level === "info" && !showInfo) return false;
      if (level === "warn" && !showWarn) return false;
      if (level === "error" && !showError) return false;
    }
    if (needle && !log.message?.toLowerCase().includes(needle)) return false;
    return true;
  });

  const goToApprovals = (log: LogEntry) => {
    if (!log.ocsf) return;
    const binary = log.ocsf.binary || "";
    let host = "";
    const m = (log.ocsf.summary || "").match(/->\s+([^\s:]+)/);
    if (m) host = m[1];
    const frag: string[] = [];
    if (binary) frag.push(`binary=${encodeURIComponent(binary)}`);
    if (host) frag.push(`host=${encodeURIComponent(host)}`);
    const hash = frag.length ? `#${frag.join("&")}` : "";
    window.location.href = `/gateways/${GW}/sandboxes/${name}/approvals${hash}`;
  };

  if (loading) return <Spinner message="Loading logs..." />;
  if (error) return <ErrorAlert message={error} />;

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-2">
        <div class="btn-group btn-group-sm" role="group">
          <input
            type="checkbox"
            class="btn-check"
            id="log-filter-info"
            checked={showInfo}
            onChange={() => setShowInfo(!showInfo)}
          />
          <label class="btn btn-outline-info" for="log-filter-info">
            Info
          </label>
          <input
            type="checkbox"
            class="btn-check"
            id="log-filter-warn"
            checked={showWarn}
            onChange={() => setShowWarn(!showWarn)}
          />
          <label class="btn btn-outline-warning" for="log-filter-warn">
            Warn
          </label>
          <input
            type="checkbox"
            class="btn-check"
            id="log-filter-error"
            checked={showError}
            onChange={() => setShowError(!showError)}
          />
          <label class="btn btn-outline-danger" for="log-filter-error">
            Error
          </label>
          <input
            type="checkbox"
            class="btn-check"
            id="log-filter-ocsf"
            checked={showOcsf}
            onChange={() => setShowOcsf(!showOcsf)}
          />
          <label class="btn btn-outline-primary" for="log-filter-ocsf">
            OCSF
          </label>
        </div>
        <div class="d-flex gap-2">
          <button
            class={`btn btn-sm ${serverOcsfOnly ? "btn-primary" : "btn-outline-primary"}`}
            title={
              serverOcsfOnly
                ? "Fetching OCSF events only. Click to fetch all."
                : "Fetch OCSF events only (server-side filter)."
            }
            onClick={() => {
              setServerOcsfOnly(!serverOcsfOnly);
              void load(!serverOcsfOnly);
            }}
          >
            <i class="bi bi-funnel" />
            <span class="ms-1">Server OCSF</span>
          </button>
          <input
            type="text"
            class="form-control form-control-sm sg-w-180"
            placeholder="Filter..."
            value={filterText}
            onInput={(e) => setFilterText((e.target as HTMLInputElement).value)}
          />
          <button class="btn btn-outline-secondary btn-sm" title="Refresh" onClick={() => void load()}>
            <i class="bi bi-arrow-clockwise" />
          </button>
        </div>
      </div>

      {showOcsf && availableClasses.length > 0 && (
        <div class="mb-2 d-flex gap-1 flex-wrap">
          {availableClasses.map((cls) => (
            <button
              key={cls}
              type="button"
              class={`btn btn-sm log-ocsf-chip ${
                classFilter[cls] === false ? "btn-outline-secondary" : "btn-primary"
              }`}
              onClick={() => setClassFilter({ ...classFilter, [cls]: !classFilter[cls] })}
            >
              {cls}
            </button>
          ))}
        </div>
      )}

      <div class="log-output" id="log-container" ref={containerRef}>
        {filteredLogs.map((log, i) => (
          <div key={i}>
            <div class={logCss(log)}>
              <span class="text-muted">{formatTimestamp(log.timestamp_ms)}</span>{" "}
              {log.ocsf ? (
                <span>
                  <span class="badge text-bg-info me-1">
                    {log.ocsf.class_prefix || "OCSF"}
                  </span>
                  {log.ocsf.activity && (
                    <span class="badge text-bg-secondary me-1">{log.ocsf.activity}</span>
                  )}
                  {log.ocsf.severity && (
                    <span class={`badge ${badgeClass("severity", log.ocsf.severity)} me-1`}>
                      {log.ocsf.severity}
                    </span>
                  )}
                  {log.ocsf.disposition && (
                    <span
                      class={`badge me-1 ${
                        log.ocsf.disposition === "ALLOWED" ? "text-bg-success" : "text-bg-danger"
                      }`}
                    >
                      {log.ocsf.disposition}
                    </span>
                  )}
                  <span>{log.ocsf.summary || log.message}</span>
                  {hasExpandable(log) && (
                    <button
                      type="button"
                      class="btn btn-sm btn-link p-0 ms-1 log-expand-btn"
                      title={expanded[i] ? "Collapse" : "Expand"}
                      onClick={() => setExpanded({ ...expanded, [i]: !expanded[i] })}
                    >
                      <i class={`bi ${expanded[i] ? "bi-chevron-up" : "bi-chevron-down"}`} />
                    </button>
                  )}
                </span>
              ) : (
                <span>
                  <span class="badge text-bg-secondary me-1">{log.source || "gateway"}</span>
                  <span>{log.message}</span>
                </span>
              )}
            </div>
            {log.ocsf && expanded[i] && (
              <div class="log-ocsf-details">
                {Object.keys(log.ocsf.bracket_fields ?? {}).length > 0 && (
                  <FieldsList fields={log.ocsf.bracket_fields!} />
                )}
                {Object.keys(log.ocsf.fields ?? {}).length > 0 && (
                  <FieldsList fields={log.ocsf.fields!} />
                )}
                <div class="log-ocsf-raw text-muted">{log.message}</div>
                {isDeniedOcsf(log) && (
                  <div class="mt-1">
                    <button
                      type="button"
                      class="btn btn-sm btn-outline-warning log-crosslink-btn"
                      title="Jump to the matching chunk in the approvals page"
                      onClick={() => goToApprovals(log)}
                    >
                      <i class="bi bi-arrow-up-right-square me-1" />
                      Find in approvals
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        {filteredLogs.length === 0 && (
          <div class="text-muted">No logs match the current filters.</div>
        )}
      </div>
    </div>
  );
}
