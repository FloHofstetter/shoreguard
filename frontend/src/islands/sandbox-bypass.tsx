/** Bypass detection dashboard (island). */

import { useEffect, useRef, useState } from "preact/hooks";

import { GW } from "../lib/constants";
import { Spinner } from "../lib/widgets";

const MITRE_LOOKUP: Record<string, string> = {
  iptables: "T1562.004",
  nftables: "T1562.004",
  nsenter: "T1611",
  unshare: "T1611",
  netns: "T1611",
  ip_route: "T1562.004",
  bypass: "T1562.004",
};

const SEVERITY_LEVELS = ["FATAL", "CRIT", "HIGH", "MED", "LOW", "INFO"];

const SEVERITY_BADGE: Record<string, string> = {
  FATAL: "bg-dark text-white",
  CRIT: "bg-danger",
  HIGH: "bg-warning text-dark",
  MED: "bg-info text-dark",
  LOW: "bg-secondary",
  INFO: "bg-light text-dark",
};

const SEVERITY_BTN: Record<string, string> = {
  FATAL: "btn-dark",
  CRIT: "btn-danger",
  HIGH: "btn-warning",
  MED: "btn-info",
  LOW: "btn-secondary",
  INFO: "btn-light",
};

interface BypassEvent {
  timestamp_ms?: number;
  event: {
    severity: string;
    technique?: string;
    mitre_id?: string;
    binary?: string;
    summary?: string;
  };
}

interface Summary {
  total: number;
  by_technique: Record<string, number>;
  by_severity: Record<string, number>;
  latest_timestamp_ms: number | null;
}

function formatTime(ms: number | undefined): string {
  if (!ms) return "—";
  const d = new Date(ms);
  return (
    d.toLocaleTimeString(undefined, { hour12: false }) +
    "." +
    String(d.getMilliseconds()).padStart(3, "0")
  );
}

export default function SandboxBypassPage({ name }: { name: string }) {
  const [loading, setLoading] = useState(false);
  const [events, setEvents] = useState<BypassEvent[]>([]);
  const [summary, setSummary] = useState<Summary>({
    total: 0,
    by_technique: {},
    by_severity: {},
    latest_timestamp_ms: null,
  });
  const [sevFilter, setSevFilter] = useState<Record<string, boolean>>(
    Object.fromEntries(SEVERITY_LEVELS.map((s) => [s, true])),
  );
  const [autoRefresh, setAutoRefresh] = useState(false);
  const timerRef = useRef<number | undefined>(undefined);

  const load = async () => {
    setLoading(true);
    try {
      const base = `/api/gateways/${encodeURIComponent(GW)}/sandboxes/${encodeURIComponent(name)}`;
      const [evtRes, sumRes] = await Promise.all([
        fetch(`${base}/bypass?limit=500`),
        fetch(`${base}/bypass/summary`),
      ]);
      if (evtRes.ok) {
        const data = await evtRes.json();
        setEvents(data.events ?? []);
      }
      if (sumRes.ok) {
        setSummary(await sumRes.json());
      }
    } catch (e) {
      console.error("Failed to load bypass events:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  useEffect(() => {
    if (autoRefresh) {
      timerRef.current = window.setInterval(() => void load(), 5000);
    }
    return () => clearInterval(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh]);

  const filteredEvents = events.filter((evt) => sevFilter[evt.event.severity] !== false);

  return (
    <div>
      <div class="row g-3 mb-3">
        <div class="col-sm-3">
          <div class={`card text-center ${summary.total > 0 ? "border-danger" : ""}`}>
            <div class="card-body py-2">
              <div class={`fs-3 fw-bold ${summary.total > 0 ? "text-danger" : "text-success"}`}>
                {summary.total}
              </div>
              <div class="text-muted small">Total Events</div>
            </div>
          </div>
        </div>
        <div class="col-sm-9">
          <div class="card">
            <div class="card-body py-2">
              <div class="d-flex gap-3 flex-wrap">
                {Object.entries(summary.by_technique ?? {}).map(([tech, count]) => (
                  <div key={tech} class="d-flex align-items-center gap-1">
                    <span class="badge bg-danger">{count}</span>
                    <span class="small fw-medium">{tech}</span>
                    {MITRE_LOOKUP[tech] && (
                      <span class="text-muted small">({MITRE_LOOKUP[tech]})</span>
                    )}
                  </div>
                ))}
                {Object.keys(summary.by_technique ?? {}).length === 0 && (
                  <div class="text-success small">
                    <i class="bi bi-check-circle me-1" />
                    No bypass attempts detected
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="d-flex justify-content-between align-items-center mb-2">
        <div class="d-flex gap-2 align-items-center">
          <span class="text-muted small">Severity:</span>
          {SEVERITY_LEVELS.map((sev) => (
            <button
              key={sev}
              type="button"
              class={`btn btn-sm ${
                sevFilter[sev] ? (SEVERITY_BTN[sev] ?? "btn-secondary") : "btn-outline-secondary"
              }`}
              onClick={() => setSevFilter({ ...sevFilter, [sev]: !sevFilter[sev] })}
            >
              {sev}
            </button>
          ))}
        </div>
        <div class="d-flex gap-2">
          <button class="btn btn-outline-secondary btn-sm" title="Refresh" onClick={() => void load()}>
            <i class="bi bi-arrow-clockwise" />
          </button>
          <label class="form-check form-switch mb-0 d-flex align-items-center">
            <input
              class="form-check-input"
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh((e.target as HTMLInputElement).checked)}
            />
            <span class="form-check-label ms-1 small">Auto</span>
          </label>
        </div>
      </div>

      {loading && events.length === 0 && <Spinner message="Loading bypass events..." />}

      {(!loading || events.length > 0) && (
        <div>
          <div class="table-responsive">
            <table class="table table-sm table-hover align-middle">
              <thead>
                <tr>
                  <th style="width:160px">Time</th>
                  <th style="width:80px">Severity</th>
                  <th style="width:120px">Technique</th>
                  <th style="width:100px">MITRE</th>
                  <th>Binary</th>
                  <th>Summary</th>
                </tr>
              </thead>
              <tbody>
                {filteredEvents.map((evt, i) => (
                  <tr key={i}>
                    <td class="text-muted small font-monospace">{formatTime(evt.timestamp_ms)}</td>
                    <td>
                      <span class={`badge ${SEVERITY_BADGE[evt.event.severity] ?? "bg-secondary"}`}>
                        {evt.event.severity}
                      </span>
                    </td>
                    <td>
                      <span class="badge bg-dark">{evt.event.technique}</span>
                    </td>
                    <td>
                      {evt.event.mitre_id && (
                        <span class="small font-monospace">{evt.event.mitre_id}</span>
                      )}
                    </td>
                    <td
                      class="small font-monospace text-truncate"
                      style="max-width:200px"
                      title={evt.event.binary || ""}
                    >
                      {evt.event.binary || "—"}
                    </td>
                    <td class="small text-truncate" style="max-width:300px" title={evt.event.summary}>
                      {evt.event.summary}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {filteredEvents.length === 0 && events.length > 0 && (
            <div class="text-muted text-center py-3">
              No events match the current severity filter.
            </div>
          )}
          {events.length === 0 && !loading && (
            <div class="text-center py-5">
              <i class="bi bi-shield-check fs-1 text-success" />
              <p class="text-muted mt-2">No bypass attempts detected for this sandbox.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
