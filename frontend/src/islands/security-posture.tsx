/** Security posture self-check page (island): "am I exposed?" */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";

interface PostureCheck {
  id: string;
  severity: "ok" | "info" | "warn" | "error";
  title: string;
  detail: string;
  fix: string | null;
}

interface PostureReport {
  checks: PostureCheck[];
  summary: Record<string, number>;
  tailscale: boolean;
}

const SEVERITY_BADGE: Record<PostureCheck["severity"], string> = {
  ok: "text-bg-success",
  info: "text-bg-info",
  warn: "text-bg-warning",
  error: "text-bg-danger",
};

const SEVERITY_ICON: Record<PostureCheck["severity"], string> = {
  ok: "check-circle",
  info: "info-circle",
  warn: "exclamation-triangle",
  error: "x-octagon",
};

const SEVERITY_ORDER: Record<PostureCheck["severity"], number> = {
  error: 0,
  warn: 1,
  info: 2,
  ok: 3,
};

function SummaryBanner({ report }: { report: PostureReport }) {
  const errors = report.summary.error ?? 0;
  const warns = report.summary.warn ?? 0;
  if (errors > 0) {
    return (
      <div class="alert alert-danger">
        <i class="bi bi-x-octagon me-2" />
        <strong>
          {errors} critical finding{errors > 1 ? "s" : ""}
        </strong>{" "}
        — this deployment has the kind of misconfiguration behind the exposed
        agent-gateway incidents. Fix the red items below first.
      </div>
    );
  }
  if (warns > 0) {
    return (
      <div class="alert alert-warning">
        <i class="bi bi-exclamation-triangle me-2" />
        No critical findings, {warns} item{warns > 1 ? "s" : ""} worth reviewing.
      </div>
    );
  }
  return (
    <div class="alert alert-success">
      <i class="bi bi-shield-check me-2" />
      All checks passed — authentication, transport, and exposure look sane.
    </div>
  );
}

export default function SecurityPosturePage() {
  const [report, setReport] = useState<PostureReport | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    apiFetch<PostureReport>(`/api/security/posture`)
      .then(setReport)
      .catch((e: Error) => setError(e.message));
  };
  useEffect(load, []);

  if (error) {
    return (
      <div class="alert alert-danger">
        <i class="bi bi-exclamation-triangle me-2" />
        {error}
      </div>
    );
  }
  if (!report) {
    return (
      <div class="text-center text-muted py-5">
        <div class="spinner-border spinner-border-sm me-2" />
        Running checks...
      </div>
    );
  }

  const checks = [...report.checks].sort(
    (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity],
  );

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">
          <i class="bi bi-shield-check me-2" />
          Security Check
        </h5>
        <button class="btn btn-sm btn-outline-secondary" onClick={load}>
          <i class="bi bi-arrow-clockwise me-1" />
          Re-run
        </button>
      </div>
      <SummaryBanner report={report} />
      <div class="list-group">
        {checks.map((c) => (
          <div key={c.id} class="list-group-item">
            <div class="d-flex align-items-start gap-3">
              <span class={`badge ${SEVERITY_BADGE[c.severity]} mt-1`}>
                <i class={`bi bi-${SEVERITY_ICON[c.severity]} me-1`} />
                {c.severity.toUpperCase()}
              </span>
              <div class="flex-grow-1">
                <div class="fw-semibold">{c.title}</div>
                <div class="text-muted small">{c.detail}</div>
                {c.fix && (
                  <div class="small mt-1">
                    <i class="bi bi-wrench-adjustable me-1" />
                    {c.fix}
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
