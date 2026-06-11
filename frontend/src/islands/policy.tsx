/** Policy pages (islands): overview, sections, presets, revisions/diff. */

import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth, hasRole } from "../lib/auth";
import { API, GW, gwUrl, navigateTo } from "../lib/constants";
import { Modal } from "../lib/Modal";
import { showConfirm, showToast } from "../lib/notify";
import { EmptyState, ErrorAlert, Spinner } from "../lib/widgets";

interface Endpoint {
  host: string;
  port: number;
  protocol?: string;
  tls?: string;
  rules?: { allow?: { method?: string; path?: string } }[];
}

interface NetworkRule {
  name?: string;
  endpoints?: Endpoint[];
  binaries?: string[];
}

interface Policy {
  version?: number;
  network_policies?: Record<string, NetworkRule>;
  filesystem?: { read_only?: string[]; read_write?: string[] };
  process?: { run_as_user?: string; run_as_group?: string };
  landlock?: { compatibility?: string };
}

interface Pin {
  pinned_version?: number | string | null;
  pinned_by?: string;
  reason?: string;
  expires_at?: string;
}

async function fetchPin(name: string): Promise<Pin | null> {
  try {
    return await apiFetch<Pin>(`${API}/sandboxes/${name}/policy/pin`);
  } catch {
    return null;
  }
}

function PinBanner({ pin, suffix }: { pin: Pin; suffix: string }) {
  return (
    <div class="alert alert-warning d-flex align-items-center mb-3 py-2">
      <i class="bi bi-lock-fill me-2" />
      <span>
        Policy pinned at v{pin.pinned_version}. {suffix}
      </span>
    </div>
  );
}

// ── Revisions / diff modal ───────────────────────────────────────────

interface Revision {
  version?: number;
  status?: string;
  policy_hash?: string;
  created_at_ms?: number;
}

interface DiffData {
  version_a: number;
  version_b: number;
  policy_a?: Policy;
  policy_b?: Policy;
}

function DiffRow({ kind, children }: { kind: "added" | "removed" | "changed" | "unchanged"; children: ComponentChildren }) {
  const labels = { added: "Added", removed: "Removed", changed: "Changed", unchanged: "" };
  return (
    <div class={`diff-${kind} p-1 mb-1 rounded small`}>
      {kind !== "unchanged" && (
        <span class={`diff-label diff-label-${kind}`}>{labels[kind]}</span>
      )}
      {children}
    </div>
  );
}

function PolicyDiff({ data }: { data: DiffData }) {
  const netA = data.policy_a?.network_policies ?? {};
  const netB = data.policy_b?.network_policies ?? {};
  const allNetKeys = [...new Set([...Object.keys(netA), ...Object.keys(netB)])].sort();

  const fsA = data.policy_a?.filesystem ?? {};
  const fsB = data.policy_b?.filesystem ?? {};
  const roA = new Set(fsA.read_only ?? []);
  const roB = new Set(fsB.read_only ?? []);
  const rwA = new Set(fsA.read_write ?? []);
  const rwB = new Set(fsB.read_write ?? []);
  const allPaths = [...new Set([...roA, ...roB, ...rwA, ...rwB])].sort();
  const access = (p: string, ro: Set<string>, rw: Set<string>) =>
    ro.has(p) ? "ro" : rw.has(p) ? "rw" : null;
  const unchangedCount = allPaths.filter((p) => {
    const aA = access(p, roA, rwA);
    const aB = access(p, roB, rwB);
    return aA === aB && aA != null;
  }).length;

  const procA = data.policy_a?.process ?? {};
  const procB = data.policy_b?.process ?? {};
  const settingsFields: [string, string | undefined, string | undefined][] = [
    ["run_as_user", procA.run_as_user, procB.run_as_user],
    ["run_as_group", procA.run_as_group, procB.run_as_group],
    [
      "landlock.compatibility",
      data.policy_a?.landlock?.compatibility,
      data.policy_b?.landlock?.compatibility,
    ],
  ];
  const processChanges = settingsFields.filter(([, a, b]) => a !== b);

  return (
    <div>
      <h6 class="mb-3">
        v{data.version_a} → v{data.version_b}
      </h6>
      <div class="diff-section-header">
        <i class="bi bi-globe me-1" />
        Network Policies
      </div>
      {allNetKeys.length === 0 && (
        <p class="text-muted small">No network policies in either version.</p>
      )}
      {allNetKeys.map((key) => {
        const inA = key in netA;
        const inB = key in netB;
        const label = (inA ? netA[key].name : netB[key].name) || key;
        if (inA && !inB) {
          return (
            <DiffRow key={key} kind="removed">
              <strong class="ms-2">{label}</strong>
              <span class="text-muted small ms-2">
                {(netA[key].endpoints ?? []).length} endpoint(s)
              </span>
            </DiffRow>
          );
        }
        if (!inA && inB) {
          return (
            <DiffRow key={key} kind="added">
              <strong class="ms-2">{label}</strong>
              <span class="text-muted small ms-2">
                {(netB[key].endpoints ?? []).length} endpoint(s)
              </span>
            </DiffRow>
          );
        }
        const changed = JSON.stringify(netA[key]) !== JSON.stringify(netB[key]);
        if (changed) {
          return (
            <DiffRow key={key} kind="changed">
              <strong class="ms-2">{label}</strong>
              <span class="text-muted small ms-2">
                {(netA[key].endpoints ?? []).length} → {(netB[key].endpoints ?? []).length}{" "}
                endpoint(s)
              </span>
            </DiffRow>
          );
        }
        return (
          <DiffRow key={key} kind="unchanged">
            <strong>{label}</strong>
            <span class="text-muted small ms-2">unchanged</span>
          </DiffRow>
        );
      })}

      <div class="diff-section-header mt-3">
        <i class="bi bi-folder me-1" />
        Filesystem
      </div>
      {allPaths.length === 0 && (
        <p class="text-muted small">No filesystem paths in either version.</p>
      )}
      {allPaths.map((p) => {
        const accessA = access(p, roA, rwA);
        const accessB = access(p, roB, rwB);
        if (accessA && !accessB) {
          return (
            <DiffRow key={p} kind="removed">
              <code class="ms-2">{p}</code> <span class="text-muted">({accessA})</span>
            </DiffRow>
          );
        }
        if (!accessA && accessB) {
          return (
            <DiffRow key={p} kind="added">
              <code class="ms-2">{p}</code> <span class="text-muted">({accessB})</span>
            </DiffRow>
          );
        }
        if (accessA !== accessB) {
          return (
            <DiffRow key={p} kind="changed">
              <code class="ms-2">{p}</code>{" "}
              <span class="text-muted">
                {accessA} → {accessB}
              </span>
            </DiffRow>
          );
        }
        return null;
      })}
      {unchangedCount > 0 && (
        <p class="diff-unchanged small mt-1">{unchangedCount} path(s) unchanged</p>
      )}

      <div class="diff-section-header mt-3">
        <i class="bi bi-gear me-1" />
        Process & Landlock
      </div>
      {processChanges.length === 0 && (
        <p class="text-muted small">No process/landlock changes.</p>
      )}
      {processChanges.map(([field, valA, valB]) => {
        if (valA && !valB) {
          return (
            <DiffRow key={field} kind="removed">
              <strong class="ms-2">{field}</strong>: {String(valA)}
            </DiffRow>
          );
        }
        if (!valA && valB) {
          return (
            <DiffRow key={field} kind="added">
              <strong class="ms-2">{field}</strong>: {String(valB)}
            </DiffRow>
          );
        }
        return (
          <DiffRow key={field} kind="changed">
            <strong class="ms-2">{field}</strong>: {String(valA)} → {String(valB)}
          </DiffRow>
        );
      })}
    </div>
  );
}

function RevisionsModal({ sandboxName, onClose }: { sandboxName: string; onClose: () => void }) {
  const [revisions, setRevisions] = useState<Revision[] | null>(null);
  const [error, setError] = useState("");
  const [selectedA, setSelectedA] = useState<number | null>(null);
  const [selectedB, setSelectedB] = useState<number | null>(null);
  const [diff, setDiff] = useState<DiffData | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  useEffect(() => {
    apiFetch<Revision[]>(`${API}/sandboxes/${sandboxName}/policy/revisions`)
      .then((revs) => {
        setRevisions(revs);
        if (revs.length >= 2) {
          setSelectedB(revs[0].version ?? null);
          setSelectedA(revs[1].version ?? null);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [sandboxName]);

  const showDiff = async () => {
    setDiffLoading(true);
    try {
      setDiff(
        await apiFetch<DiffData>(
          `${API}/sandboxes/${sandboxName}/policy/diff?version_a=${selectedA}&version_b=${selectedB}`,
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDiffLoading(false);
    }
  };

  const canCompare = selectedA != null && selectedB != null && selectedA !== selectedB;

  return (
    <Modal
      size="lg"
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-clock-history me-2" />
          Policy Revisions
        </span>
      }
      footer={
        diff === null ? (
          <button class="btn btn-outline-info" disabled={!canCompare} onClick={() => void showDiff()}>
            <i class="bi bi-arrow-left-right me-1" />
            Compare
          </button>
        ) : (
          <button class="btn btn-outline-secondary" onClick={() => setDiff(null)}>
            <i class="bi bi-arrow-left me-1" />
            Back
          </button>
        )
      }
    >
      {error && <ErrorAlert message={error} />}
      {!error && revisions === null && <Spinner message="Loading revisions..." />}
      {!error && revisions !== null && revisions.length === 0 && (
        <EmptyState icon="clock-history" message="No policy revisions recorded." />
      )}
      {!error && diffLoading && <Spinner message="Loading policy diff..." />}
      {!error && !diffLoading && diff !== null && <PolicyDiff data={diff} />}
      {!error && !diffLoading && diff === null && revisions !== null && revisions.length > 0 && (
        <div>
          <p class="text-muted small mb-2">Select two versions to compare:</p>
          <div class="table-responsive">
            <table class="table table-striped table-sm align-middle">
              <thead>
                <tr>
                  <th class="sg-w-40">A</th>
                  <th class="sg-w-40">B</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Hash</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {revisions.map((rev) => (
                  <tr key={rev.version}>
                    <td>
                      <input
                        type="radio"
                        name="diff-a"
                        class="form-check-input"
                        checked={selectedA === rev.version}
                        onChange={() => setSelectedA(rev.version ?? null)}
                      />
                    </td>
                    <td>
                      <input
                        type="radio"
                        name="diff-b"
                        class="form-check-input"
                        checked={selectedB === rev.version}
                        onChange={() => setSelectedB(rev.version ?? null)}
                      />
                    </td>
                    <td>
                      <strong>v{rev.version ?? "—"}</strong>
                    </td>
                    <td>
                      <span class="badge text-bg-secondary">{rev.status || "—"}</span>
                    </td>
                    <td class="text-muted small font-monospace">
                      {rev.policy_hash ? rev.policy_hash.substring(0, 8) : "—"}
                    </td>
                    <td class="text-muted small">
                      {rev.created_at_ms ? new Date(rev.created_at_ms).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Modal>
  );
}

// ── GitOps YAML apply card ───────────────────────────────────────────

function YamlApplyCard({ sandboxName }: { sandboxName: string }) {
  const [expanded, setExpanded] = useState(false);
  const [yamlText, setYamlText] = useState("");
  const [applyMode, setApplyMode] = useState<"replace" | "merge">("replace");
  const [expectedVersion, setExpectedVersion] = useState("");
  const [applying, setApplying] = useState(false);
  const [dryRunMode, setDryRunMode] = useState(false);
  const [lastResult, setLastResult] = useState<{
    ok: boolean;
    status: string;
    message: string;
    diff: unknown;
  } | null>(null);

  const submit = async (dryRun: boolean) => {
    setApplying(true);
    setDryRunMode(dryRun);
    setLastResult(null);
    const body: Record<string, unknown> = { yaml: yamlText, dry_run: dryRun, mode: applyMode };
    if (expectedVersion.trim()) body.expected_version = expectedVersion.trim();
    try {
      const result = await apiFetch<{ status?: string; diff?: unknown }>(
        `${API}/sandboxes/${sandboxName}/policy/apply`,
        { method: "POST", body: JSON.stringify(body) },
      );
      setLastResult({
        ok: true,
        status: result.status || (dryRun ? "dry_run" : "applied"),
        message: dryRun
          ? "Dry-run complete. Review the diff below; no changes written."
          : "Policy applied successfully.",
        diff: result.diff ?? null,
      });
      if (!dryRun) showToast(`Policy ${applyMode}d.`, "success");
    } catch (e) {
      const msg = (e as Error).message;
      if (msg.includes("merge_unsupported")) {
        setLastResult({
          ok: false,
          status: "Merge mode not applicable",
          message:
            "This change touches filesystem, process, or landlock. Retry with Apply mode = Replace.",
          diff: null,
        });
        showToast("Merge mode cannot express this diff — use Replace.", "warning");
      } else {
        setLastResult({ ok: false, status: "Apply failed", message: msg, diff: null });
        showToast(`Apply failed: ${msg}`, "danger");
      }
    } finally {
      setApplying(false);
    }
  };

  return (
    <div class="card sg-card-themed">
      <div class="card-body">
        <div class="d-flex align-items-center mb-2">
          <i class="bi bi-cloud-upload fs-4 text-primary me-3" />
          <div class="flex-grow-1">
            <h6 class="mb-0">Apply Policy YAML</h6>
            <span class="text-muted small">
              Paste a GitOps policy document and apply it atomically. Mirrors{" "}
              <code>shoreguard policy apply</code> on the CLI.
            </span>
          </div>
          <button
            class="btn btn-sm btn-outline-secondary ms-2"
            onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
          >
            <i class={`bi ${expanded ? "bi-chevron-up" : "bi-chevron-down"}`} />
            <span> {expanded ? "Collapse" : "Expand"}</span>
          </button>
        </div>
        {expanded && (
          <div class="mt-3">
            <div class="mb-3">
              <label class="form-label small">YAML Document</label>
              <textarea
                class="form-control font-monospace"
                rows={10}
                value={yamlText}
                onInput={(e) => setYamlText((e.target as HTMLTextAreaElement).value)}
                placeholder={
                  "metadata:\n  gateway: my-gw\n  sandbox: sb1\npolicy:\n  network_policies:\n    allow-gh: {name: allow-gh, endpoints: [{host: api.github.com, port: 443}]}"
                }
              />
            </div>
            <div class="row g-3 mb-3">
              <div class="col-md-6">
                <label class="form-label small">Apply Mode</label>
                <div class="btn-group w-100" role="group" aria-label="Apply mode">
                  <input
                    type="radio"
                    class="btn-check"
                    name="apply-mode"
                    id="apply-mode-replace"
                    checked={applyMode === "replace"}
                    onChange={() => setApplyMode("replace")}
                  />
                  <label class="btn btn-outline-primary" for="apply-mode-replace">
                    <i class="bi bi-arrow-repeat me-1" />
                    Replace
                  </label>
                  <input
                    type="radio"
                    class="btn-check"
                    name="apply-mode"
                    id="apply-mode-merge"
                    checked={applyMode === "merge"}
                    onChange={() => setApplyMode("merge")}
                  />
                  <label class="btn btn-outline-primary" for="apply-mode-merge">
                    <i class="bi bi-sliders me-1" />
                    Merge
                  </label>
                </div>
                {applyMode === "merge" && (
                  <div class="form-text small">
                    Sends per-rule merge operations instead of a full rewrite. Requires gateway{" "}
                    <strong>≥ v0.0.33</strong> and cannot express filesystem / process / landlock
                    changes.
                  </div>
                )}
              </div>
              <div class="col-md-6">
                <label class="form-label small">Optimistic Lock (optional)</label>
                <input
                  type="text"
                  class="form-control font-monospace"
                  placeholder="sha256:... (leave empty to skip)"
                  value={expectedVersion}
                  onInput={(e) => setExpectedVersion((e.target as HTMLInputElement).value)}
                />
              </div>
            </div>
            <div class="d-flex gap-2">
              <button
                class="btn btn-outline-primary"
                disabled={applying || !yamlText.trim()}
                onClick={() => void submit(true)}
              >
                {applying && dryRunMode ? (
                  <span class="spinner-border spinner-border-sm me-1" />
                ) : (
                  <i class="bi bi-eye me-1" />
                )}
                Dry-Run (Diff)
              </button>
              <button
                class="btn btn-primary"
                disabled={applying || !yamlText.trim()}
                onClick={() => void submit(false)}
              >
                {applying && !dryRunMode ? (
                  <span class="spinner-border spinner-border-sm me-1" />
                ) : (
                  <i class="bi bi-cloud-upload me-1" />
                )}
                Apply ({applyMode})
              </button>
            </div>
            {lastResult && (
              <div class="mt-3">
                <div class={`alert ${lastResult.ok ? "alert-success" : "alert-warning"}`}>
                  <div class="fw-semibold">{lastResult.status}</div>
                  {lastResult.message && <div class="small mt-1">{lastResult.message}</div>}
                  {lastResult.diff != null && (
                    <pre class="mt-2 mb-0 small">{JSON.stringify(lastResult.diff, null, 2)}</pre>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Policy overview page ─────────────────────────────────────────────

export default function PolicyOverviewPage({ name }: { name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [pin, setPin] = useState<Pin | null>(null);
  const [activeVersion, setActiveVersion] = useState<number | null>(null);
  const [pinModalOpen, setPinModalOpen] = useState(false);
  const [pinReason, setPinReason] = useState("");
  const [pinExpiresAt, setPinExpiresAt] = useState("");
  const [pinning, setPinning] = useState(false);
  const [revisionsOpen, setRevisionsOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [policyData, pinData, sbData] = await Promise.allSettled([
        apiFetch<{ policy?: Policy }>(`${API}/sandboxes/${name}/policy`),
        apiFetch<Pin>(`${API}/sandboxes/${name}/policy/pin`),
        apiFetch<{ current_policy_version?: number }>(`${API}/sandboxes/${name}`),
      ]);
      if (policyData.status === "fulfilled") {
        setPolicy(policyData.value.policy ?? null);
      } else {
        setError((policyData.reason as Error)?.message || "Failed to load policy");
      }
      setPin(pinData.status === "fulfilled" ? pinData.value : null);
      if (sbData.status === "fulfilled" && sbData.value) {
        const v = sbData.value.current_policy_version;
        setActiveVersion(v == null ? null : Number(v));
      } else {
        setActiveVersion(null);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const pinPolicy = async () => {
    setPinning(true);
    try {
      const body: Record<string, unknown> = {};
      if (pinReason.trim()) body.reason = pinReason.trim();
      if (pinExpiresAt.trim()) body.expires_at = new Date(pinExpiresAt).toISOString();
      setPin(
        await apiFetch<Pin>(`${API}/sandboxes/${name}/policy/pin`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
      );
      showToast("Policy pinned.", "success");
      setPinReason("");
      setPinExpiresAt("");
      setPinModalOpen(false);
    } catch (e) {
      showToast(`Failed to pin: ${(e as Error).message}`, "danger");
    } finally {
      setPinning(false);
    }
  };

  const unpinPolicy = async () => {
    const confirmed = await showConfirm(
      "Remove the policy pin? This will allow policy modifications again.",
      { icon: "unlock", iconColor: "text-warning", btnClass: "btn-warning", btnLabel: "Unpin" },
    );
    if (!confirmed) return;
    try {
      await apiFetch(`${API}/sandboxes/${name}/policy/pin`, { method: "DELETE" });
      setPin(null);
      showToast("Policy unpinned.", "success");
    } catch (e) {
      showToast(`Failed to unpin: ${(e as Error).message}`, "danger");
    }
  };

  if (loading) return <Spinner message="Loading policy..." />;
  if (error) return <ErrorAlert message={error} />;
  if (!policy) {
    return (
      <div class="text-muted py-4">
        <i class="bi bi-info-circle me-1" />
        No policy data available.
      </div>
    );
  }

  const networkCount = Object.keys(policy.network_policies ?? {}).length;
  const fsCount =
    (policy.filesystem?.read_only ?? []).length + (policy.filesystem?.read_write ?? []).length;
  let procCount = 0;
  if (policy.process?.run_as_user) procCount++;
  if (policy.process?.run_as_group) procCount++;
  if (policy.landlock?.compatibility) procCount++;

  const pinnedVersion = pin?.pinned_version != null ? Number(pin.pinned_version) : null;
  const hasPinDrift =
    pin !== null &&
    pinnedVersion !== null &&
    activeVersion !== null &&
    pinnedVersion !== activeVersion;

  const sectionCard = (path: string, icon: string, color: string, title: string, count: number, label: string, footer: string) => (
    <div class="col-md-4">
      <a
        href={gwUrl(`/sandboxes/${name}${path}`)}
        class="card text-decoration-none policy-overview-card sg-card-themed h-100"
      >
        <div class="card-body">
          <div class="d-flex align-items-center mb-2">
            <i class={`bi ${icon} ${color} me-2`} />
            <h6 class="mb-0">{title}</h6>
          </div>
          <div class="fs-2 fw-bold mb-1">{count}</div>
          <span class="text-muted small">{label} configured</span>
        </div>
        <div class="card-footer border-0 pt-0 small bg-transparent">
          {footer} <i class="bi bi-arrow-right" />
        </div>
      </a>
    </div>
  );

  return (
    <div>
      {pin && (
        <div
          class={`alert d-flex align-items-center mb-3 ${hasPinDrift ? "alert-danger" : "alert-warning"}`}
        >
          <i class={`bi me-2 fs-5 ${hasPinDrift ? "bi-exclamation-triangle-fill" : "bi-lock-fill"}`} />
          <div class="flex-grow-1">
            <strong>{hasPinDrift ? "Policy drift detected" : "Policy pinned"}</strong>{" "}
            {!hasPinDrift ? (
              <span>
                at version {pinnedVersion ?? "?"} by {pin.pinned_by}.
                {pin.reason && <span class="ms-1">{pin.reason}</span>}
                {pin.expires_at && (
                  <span class="ms-1 text-muted small">
                    (expires {new Date(pin.expires_at).toLocaleString()})
                  </span>
                )}
              </span>
            ) : (
              <span>
                Pinned at <span class="badge text-bg-warning">v{pinnedVersion}</span> but
                supervisor is loaded on <span class="badge text-bg-danger">v{activeVersion}</span>.
                The pinned revision has not yet been enforced on the sandbox.
                {pin.reason && <span class="ms-1 small">({pin.reason})</span>}
              </span>
            )}
          </div>
          <button class="btn btn-sm btn-outline-warning ms-2" onClick={() => void unpinPolicy()}>
            <i class="bi bi-unlock me-1" />
            Unpin
          </button>
        </div>
      )}

      {!pin && activeVersion !== null && (
        <div class="mb-3">
          <span class="text-muted small">
            <i class="bi bi-activity me-1" />
            Supervisor-loaded revision: <span class="badge text-bg-secondary">v{activeVersion}</span>
          </span>
        </div>
      )}

      <div class="d-flex justify-content-between align-items-center mb-4">
        <span class="text-muted">Policy v{policy.version ?? "?"}</span>
        <div class="d-flex gap-2">
          {!pin && (
            <button class="btn btn-outline-warning btn-sm" onClick={() => setPinModalOpen(true)}>
              <i class="bi bi-lock me-1" />
              Pin
            </button>
          )}
          <button class="btn btn-outline-secondary btn-sm" onClick={() => setRevisionsOpen(true)}>
            <i class="bi bi-clock-history me-1" />
            Revisions
          </button>
        </div>
      </div>

      <div class="row g-3 mb-4">
        {sectionCard(
          "/network-policies",
          "bi-globe",
          "text-info",
          "Network Policies",
          networkCount,
          networkCount === 1 ? "1 rule" : `${networkCount} rules`,
          "Manage rules",
        )}
        {sectionCard(
          "/filesystem-policy",
          "bi-folder",
          "text-warning",
          "Filesystem",
          fsCount,
          fsCount === 1 ? "1 path" : `${fsCount} paths`,
          "View paths",
        )}
        {sectionCard(
          "/process-policy",
          "bi-cpu",
          "text-success",
          "Process & Landlock",
          procCount,
          procCount === 1 ? "1 setting" : `${procCount} settings`,
          "View settings",
        )}
      </div>

      <div class="row g-3 mt-0">
        <div class="col-12">
          <a
            href={gwUrl(`/sandboxes/${name}/apply-preset`)}
            class="card text-decoration-none policy-overview-card sg-card-themed"
          >
            <div class="card-body d-flex align-items-center">
              <div class="me-3">
                <i class="bi bi-shield-plus fs-4 text-info" />
              </div>
              <div class="flex-grow-1">
                <h6 class="mb-0">Apply Preset</h6>
                <span class="text-muted small">
                  Add predefined network rules from a template
                </span>
              </div>
              <i class="bi bi-arrow-right" />
            </div>
          </a>
        </div>
      </div>

      <div class="row g-3 mt-0">
        <div class="col-12">
          <YamlApplyCard sandboxName={name} />
        </div>
      </div>

      {pinModalOpen && (
        <Modal
          onClose={() => setPinModalOpen(false)}
          title={
            <span>
              <i class="bi bi-lock me-2" />
              Pin Policy
            </span>
          }
          footer={
            <button class="btn btn-warning" onClick={() => void pinPolicy()} disabled={pinning}>
              {pinning ? (
                <span class="spinner-border spinner-border-sm me-1" />
              ) : (
                <i class="bi bi-lock me-1" />
              )}
              Pin Policy
            </button>
          }
        >
          <p class="text-muted small">
            Pinning locks the current policy version. All modifications (rule edits, approvals,
            presets) will be blocked until unpinned.
          </p>
          <div class="mb-3">
            <label class="form-label">
              Reason <span class="text-muted">(optional)</span>
            </label>
            <input
              type="text"
              class="form-control"
              placeholder="e.g. deploy freeze, audit window"
              value={pinReason}
              onInput={(e) => setPinReason((e.target as HTMLInputElement).value)}
            />
          </div>
          <div class="mb-3">
            <label class="form-label">
              Expires at <span class="text-muted">(optional)</span>
            </label>
            <input
              type="datetime-local"
              class="form-control"
              value={pinExpiresAt}
              onInput={(e) => setPinExpiresAt((e.target as HTMLInputElement).value)}
            />
          </div>
        </Modal>
      )}
      {revisionsOpen && (
        <RevisionsModal sandboxName={name} onClose={() => setRevisionsOpen(false)} />
      )}
    </div>
  );
}

// ── Network section ──────────────────────────────────────────────────

export function NetworkPoliciesSection({ name }: { name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pin, setPin] = useState<Pin | null>(null);
  const [rules, setRules] = useState<
    { key: string; name: string; showKey: boolean; binaries: string[]; topHosts: string }[]
  >([]);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      try {
        const [data, pinValue] = await Promise.all([
          apiFetch<{ policy?: Policy }>(`${API}/sandboxes/${name}/policy`).catch((e: Error) => {
            setError(e.message || "Failed to load policy");
            return null;
          }),
          fetchPin(name),
        ]);
        setPin(pinValue);
        const networkRules = data?.policy?.network_policies ?? {};
        setRules(
          Object.entries(networkRules).map(([key, rule]) => {
            const endpoints = rule.endpoints ?? [];
            const more = endpoints.length > 2 ? ` +${endpoints.length - 2}` : "";
            return {
              key,
              name: rule.name || key,
              showKey: key !== rule.name && key !== (rule.name || "").replace(/-/g, "_"),
              binaries: rule.binaries ?? [],
              topHosts: endpoints.slice(0, 2).map((ep) => ep.host).join(", ") + more,
            };
          }),
        );
      } finally {
        setLoading(false);
      }
    })();
  }, [name]);

  if (loading) return <Spinner message="Loading network policies..." />;
  if (error) return <ErrorAlert message={error} />;

  const addRuleHref = gwUrl(`/sandboxes/${name}/rules/_new`);

  return (
    <div>
      {pin && <PinBanner pin={pin} suffix="Editing disabled." />}
      <div class="d-flex justify-content-end mb-3">
        <a href={addRuleHref} class={`btn btn-outline-success btn-sm ${pin ? "disabled" : ""}`}>
          <i class="bi bi-plus me-1" />
          Add Rule
        </a>
      </div>
      {rules.length === 0 && (
        <EmptyState icon="globe" message="No network policies configured.">
          <a href={addRuleHref} class="btn btn-outline-success btn-sm">
            <i class="bi bi-plus me-1" />
            Add Rule
          </a>
        </EmptyState>
      )}
      {rules.length > 0 && (
        <div class="table-responsive">
          <table class="table table-striped table-hover table-sm align-middle table-clickable">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Endpoints</th>
                <th>Binaries</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr
                  key={rule.key}
                  onClick={() =>
                    navigateTo(gwUrl(`/sandboxes/${name}/rules/${encodeURIComponent(rule.key)}`))
                  }
                >
                  <td>
                    <strong>{rule.name}</strong>
                    {rule.showKey && <div class="text-muted small">{rule.key}</div>}
                  </td>
                  <td>
                    <span class="text-muted small font-monospace">{rule.topHosts}</span>
                  </td>
                  <td>
                    <span class="badge text-bg-secondary">{rule.binaries.length}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Filesystem section ───────────────────────────────────────────────

export function FilesystemPolicySection({ name }: { name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pin, setPin] = useState<Pin | null>(null);
  const [rows, setRows] = useState<{ path: string; label: string; badge: string }[]>([]);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [newAccess, setNewAccess] = useState("ro");
  const [isOperator, setIsOperator] = useState(false);
  const pathInputRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    const [data, pinValue] = await Promise.all([
      apiFetch<{ policy?: Policy }>(`${API}/sandboxes/${name}/policy`).catch((e: Error) => {
        setError(e.message || "Failed to load policy");
        return null;
      }),
      fetchPin(name),
    ]);
    setPin(pinValue);
    const fs = data?.policy?.filesystem;
    const nextRows: typeof rows = [];
    for (const path of fs?.read_only ?? []) {
      nextRows.push({ path, label: "Read Only", badge: "text-bg-warning" });
    }
    for (const path of fs?.read_write ?? []) {
      nextRows.push({ path, label: "Read / Write", badge: "text-bg-success" });
    }
    setRows(nextRows);
    setLoading(false);
  };

  useEffect(() => {
    void load();
    void ensureAuth().then(() => setIsOperator(hasRole("operator")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const addPath = async () => {
    const path = newPath.trim();
    if (!path) {
      showToast("Path is required.", "warning");
      return;
    }
    try {
      await apiFetch(`${API}/sandboxes/${name}/policy/filesystem`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, access: newAccess }),
      });
      showToast(`Path "${path}" added.`, "success");
      setShowAddForm(false);
      setNewPath("");
      await load();
    } catch (e) {
      showToast(`Failed: ${(e as Error).message}`, "danger");
    }
  };

  const deletePath = async (path: string) => {
    const confirmed = await showConfirm(`Remove filesystem path "${path}"?`, {
      icon: "trash",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Remove",
    });
    if (!confirmed) return;
    try {
      await apiFetch(
        `${API}/sandboxes/${name}/policy/filesystem?path=${encodeURIComponent(path)}`,
        { method: "DELETE" },
      );
      showToast(`Path "${path}" removed.`, "success");
      await load();
    } catch (e) {
      showToast(`Failed: ${(e as Error).message}`, "danger");
    }
  };

  if (loading) return <Spinner message="Loading filesystem policy..." />;
  if (error) return <ErrorAlert message={error} />;

  return (
    <div>
      {pin && <PinBanner pin={pin} suffix="Editing disabled." />}
      <div class="d-flex justify-content-between align-items-center mb-3">
        <span class="text-muted small">
          <i class="bi bi-info-circle me-1" />
          Add or remove filesystem paths.
        </span>
        {isOperator && (
          <button
            class="btn btn-outline-success btn-sm"
            disabled={Boolean(pin)}
            onClick={() => {
              if (pin) {
                showToast("Policy is pinned. Unpin to edit.", "warning");
                return;
              }
              setShowAddForm(true);
              requestAnimationFrame(() => pathInputRef.current?.focus());
            }}
          >
            <i class="bi bi-plus me-1" />
            Add Path
          </button>
        )}
      </div>

      {showAddForm && (
        <div class="card sg-card-themed mb-3">
          <div class="card-body sg-overlay-card">
            <div class="row g-2 align-items-end">
              <div class="col-md-6">
                <label class="form-label small">Path</label>
                <input
                  type="text"
                  ref={pathInputRef}
                  class="form-control form-control-sm font-monospace"
                  placeholder="/usr/local/bin"
                  value={newPath}
                  onInput={(e) => setNewPath((e.target as HTMLInputElement).value)}
                />
              </div>
              <div class="col-md-3">
                <label class="form-label small">Access</label>
                <select
                  class="form-select form-select-sm"
                  value={newAccess}
                  onChange={(e) => setNewAccess((e.target as HTMLSelectElement).value)}
                >
                  <option value="ro">Read Only</option>
                  <option value="rw">Read / Write</option>
                </select>
              </div>
              <div class="col-md-3 d-flex gap-2">
                <button class="btn btn-success btn-sm" onClick={() => void addPath()}>
                  <i class="bi bi-check me-1" />
                  Add
                </button>
                <button class="btn btn-outline-secondary btn-sm" onClick={() => setShowAddForm(false)}>
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {rows.length > 0 ? (
        <div class="table-responsive">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th>Path</th>
                <th class="sg-w-120">Access</th>
                <th class="sg-w-60 text-end">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.path}>
                  <td class="font-monospace small">{row.path}</td>
                  <td>
                    <span class={`badge ${row.badge}`}>{row.label}</span>
                  </td>
                  <td class="text-end">
                    {isOperator && (
                      <button
                        class="btn btn-sm text-muted"
                        title="Delete"
                        onClick={() => void deletePath(row.path)}
                      >
                        <i class="bi bi-trash3" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState icon="folder" message="No filesystem paths configured." />
      )}
    </div>
  );
}

// ── Process section ──────────────────────────────────────────────────

export function ProcessPolicySection({ name }: { name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pin, setPin] = useState<Pin | null>(null);
  const [editing, setEditing] = useState(false);
  const [runAsUser, setRunAsUser] = useState("");
  const [runAsGroup, setRunAsGroup] = useState("");
  const [landlockCompat, setLandlockCompat] = useState("");
  const [isOperator, setIsOperator] = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    const [data, pinValue] = await Promise.all([
      apiFetch<{ policy?: Policy }>(`${API}/sandboxes/${name}/policy`).catch((e: Error) => {
        setError(e.message || "Failed to load policy");
        return null;
      }),
      fetchPin(name),
    ]);
    setPin(pinValue);
    const policy = data?.policy ?? {};
    setRunAsUser(policy.process?.run_as_user ?? "");
    setRunAsGroup(policy.process?.run_as_group ?? "");
    setLandlockCompat(policy.landlock?.compatibility ?? "");
    setLoading(false);
  };

  useEffect(() => {
    void load();
    void ensureAuth().then(() => setIsOperator(hasRole("operator")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  const save = async () => {
    try {
      await apiFetch(`${API}/sandboxes/${name}/policy/process`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_as_user: runAsUser.trim() || null,
          run_as_group: runAsGroup.trim() || null,
          landlock_compatibility: landlockCompat.trim() || null,
        }),
      });
      showToast("Process policy updated.", "success");
      setEditing(false);
      await load();
    } catch (e) {
      showToast(`Failed: ${(e as Error).message}`, "danger");
    }
  };

  if (loading) return <Spinner message="Loading process policy..." />;
  if (error) return <ErrorAlert message={error} />;

  const valueCell = (value: string) =>
    value ? <span>{value}</span> : <span class="text-muted">—</span>;

  return (
    <div>
      {pin && <PinBanner pin={pin} suffix="Editing disabled." />}
      <div class="d-flex justify-content-between align-items-center mb-3">
        <span class="text-muted small">
          <i class="bi bi-cpu me-1" />
          Process and Landlock settings
        </span>
        {isOperator && (
          <button
            class="btn btn-outline-secondary btn-sm"
            disabled={Boolean(pin)}
            onClick={() => setEditing(!editing)}
          >
            <i class={`bi me-1 ${editing ? "bi-x" : "bi-pencil"}`} />
            <span>{editing ? "Cancel" : "Edit"}</span>
          </button>
        )}
      </div>

      {!editing ? (
        <div class="table-responsive">
          <table class="table table-sm align-middle">
            <thead>
              <tr>
                <th>Setting</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Run as user</td>
                <td class="font-monospace">{valueCell(runAsUser)}</td>
              </tr>
              <tr>
                <td>Run as group</td>
                <td class="font-monospace">{valueCell(runAsGroup)}</td>
              </tr>
              <tr>
                <td>Landlock compatibility</td>
                <td class="font-monospace">{valueCell(landlockCompat)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div class="card sg-card-themed">
          <div class="card-body sg-overlay-card">
            <div class="row g-3">
              <div class="col-md-4">
                <label class="form-label small">Run as user</label>
                <input
                  type="text"
                  class="form-control form-control-sm font-monospace"
                  placeholder="e.g. 1000"
                  value={runAsUser}
                  onInput={(e) => setRunAsUser((e.target as HTMLInputElement).value)}
                />
              </div>
              <div class="col-md-4">
                <label class="form-label small">Run as group</label>
                <input
                  type="text"
                  class="form-control form-control-sm font-monospace"
                  placeholder="e.g. 1000"
                  value={runAsGroup}
                  onInput={(e) => setRunAsGroup((e.target as HTMLInputElement).value)}
                />
              </div>
              <div class="col-md-4">
                <label class="form-label small">Landlock compatibility</label>
                <input
                  type="text"
                  class="form-control form-control-sm font-monospace"
                  placeholder="e.g. 3"
                  value={landlockCompat}
                  onInput={(e) => setLandlockCompat((e.target as HTMLInputElement).value)}
                />
              </div>
            </div>
            <div class="mt-3 d-flex gap-2">
              <button class="btn btn-success btn-sm" onClick={() => void save()}>
                <i class="bi bi-check me-1" />
                Save
              </button>
              <button class="btn btn-outline-secondary btn-sm" onClick={() => setEditing(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Apply-preset section ─────────────────────────────────────────────

interface Preset {
  name: string;
  description?: string;
}

interface PresetPreview {
  preset: string;
  adds: string[];
  overwrites: string[];
  rules: Record<string, string[]>;
}

export function ApplyPresetSection({ name }: { name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [presets, setPresets] = useState<Preset[]>([]);
  const [pin, setPin] = useState<Pin | null>(null);
  const [preview, setPreview] = useState<PresetPreview | null>(null);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      const [presetsValue, pinValue] = await Promise.all([
        apiFetch<Preset[]>(`/api/policies/presets`).catch((e: Error) => {
          setError(e.message || "Failed to load presets");
          return [] as Preset[];
        }),
        fetchPin(name),
      ]);
      setPresets(presetsValue);
      setPin(pinValue);
      setLoading(false);
    })();
  }, [name]);

  const loadPreview = async (presetName: string) => {
    try {
      const p = await apiFetch<PresetPreview>(
        `${API}/sandboxes/${name}/policy/presets/${presetName}/preview`,
      );
      setPreview(p);
    } catch (e) {
      showToast(`Preview failed: ${(e as Error).message}`, "danger");
    }
  };

  const apply = async (presetName: string) => {
    if (pin) {
      showToast("Policy is pinned. Unpin to apply presets.", "warning");
      return;
    }
    const confirmed = await showConfirm(`Apply "${presetName}" preset to ${name}?`, {
      icon: "shield-plus",
      iconColor: "text-success",
      btnClass: "btn-success",
      btnLabel: "Apply",
    });
    if (!confirmed) return;
    try {
      await apiFetch(`${API}/sandboxes/${name}/policy/presets/${presetName}`, { method: "POST" });
      showToast(`Preset "${presetName}" applied.`, "success");
      navigateTo(gwUrl(`/sandboxes/${name}/policy`));
    } catch (e) {
      showToast(`Failed to apply preset: ${(e as Error).message}`, "danger");
    }
  };

  if (loading) return <Spinner message="Loading presets..." />;
  if (error) return <ErrorAlert message={error} />;

  return (
    <div>
      {pin && <PinBanner pin={pin} suffix="Preset application disabled." />}
      {presets.length === 0 && <EmptyState icon="shield-plus" message="No presets available." />}
      {presets.length > 0 && (
        <div class="row g-3">
          {presets.map((p) => (
            <div key={p.name} class="col-md-4">
              <div class="card h-100 policy-overview-card sg-card-themed">
                <div class="card-body">
                  <h6 class="mb-2">{p.name}</h6>
                  <p class="text-muted small mb-0">{p.description || ""}</p>
                </div>
                <div class="card-footer border-0 pt-0 bg-transparent d-flex gap-2">
                  <button
                    class="btn btn-outline-secondary btn-sm"
                    onClick={() => void loadPreview(p.name)}
                  >
                    <i class="bi bi-eye me-1" />
                    Preview
                  </button>
                  <button
                    class="btn btn-outline-success btn-sm"
                    disabled={Boolean(pin)}
                    onClick={() => void apply(p.name)}
                  >
                    <i class="bi bi-plus me-1" />
                    Apply
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {preview && (
        <div class="card sg-card-themed mt-3">
          <div class="card-body">
            <div class="d-flex justify-content-between align-items-center mb-2">
              <h6 class="mb-0">
                <i class="bi bi-eye me-2" />
                Preview: {preview.preset} → {name}
              </h6>
              <button
                class="btn btn-sm btn-outline-secondary"
                onClick={() => setPreview(null)}
                aria-label="Close preview"
              >
                <i class="bi bi-x-lg" />
              </button>
            </div>
            {preview.adds.length === 0 && preview.overwrites.length === 0 && (
              <div class="text-muted small">This preset would change nothing.</div>
            )}
            {Object.entries(preview.rules).map(([key, endpoints]) => (
              <div key={key} class="small mb-1">
                {preview.overwrites.includes(key) ? (
                  <span class="badge text-bg-warning me-2">overwrites</span>
                ) : (
                  <span class="badge text-bg-success me-2">adds</span>
                )}
                <code>{key}</code>
                <span class="text-muted ms-2">{endpoints.join(", ")}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Global presets list + preset detail ──────────────────────────────

export function PresetsListPage() {
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch<Preset[]>(`/api/policies/presets`)
      .then(setPresets)
      .catch((e: Error) => {
        setError(e.message);
        setPresets([]);
      });
  }, []);

  if (presets === null) return <Spinner />;
  if (error) return <ErrorAlert message={error} />;

  return (
    <div>
      <h5 class="mb-3">
        <i class="bi bi-shield-lock me-2" />
        Policy Presets
      </h5>
      {presets.length === 0 && <EmptyState icon="shield-plus" message="No presets available." />}
      {presets.length > 0 && (
        <div class="table-responsive">
          <table class="table table-striped table-hover table-sm align-middle table-clickable">
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {presets.map((p) => (
                <tr key={p.name} onClick={() => navigateTo(`/policies/${p.name}`)}>
                  <td>
                    <strong>{p.name}</strong>
                  </td>
                  <td class="text-muted small">{p.description || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

interface SandboxSummary {
  name: string;
  phase: string;
}

export function PresetDetailPage({ presetName }: { presetName: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [meta, setMeta] = useState<{ name?: string; description?: string }>({});
  const [ruleEntries, setRuleEntries] = useState<
    { key: string; name: string; endpoints: Endpoint[] }[]
  >([]);
  const [sandboxes, setSandboxes] = useState<SandboxSummary[]>([]);
  const [selectedSandbox, setSelectedSandbox] = useState("");

  useEffect(() => {
    void (async () => {
      setLoading(true);
      setError("");
      try {
        const data = await apiFetch<{
          preset?: { name?: string; description?: string };
          network_policies?: Record<string, NetworkRule>;
        }>(`/api/policies/presets/${presetName}`);
        setMeta(data.preset ?? {});
        setRuleEntries(
          Object.entries(data.network_policies ?? {}).map(([key, rule]) => ({
            key,
            name: rule.name || key,
            endpoints: rule.endpoints ?? [],
          })),
        );
        if (GW) {
          try {
            const resp = await apiFetch<SandboxSummary[] | { items?: SandboxSummary[] }>(
              `${API}/sandboxes`,
            );
            const all = Array.isArray(resp) ? resp : (resp.items ?? []);
            setSandboxes(all.filter((sb) => sb.phase === "ready"));
          } catch {
            setSandboxes([]);
          }
        }
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    })();
  }, [presetName]);

  const applyToSandbox = async () => {
    if (!selectedSandbox) return;
    const confirmed = await showConfirm(`Apply "${presetName}" preset to ${selectedSandbox}?`, {
      icon: "shield-plus",
      iconColor: "text-success",
      btnClass: "btn-success",
      btnLabel: "Apply",
    });
    if (!confirmed) return;
    try {
      await apiFetch(`${API}/sandboxes/${selectedSandbox}/policy/presets/${presetName}`, {
        method: "POST",
      });
      showToast(`Preset "${presetName}" applied.`, "success");
      navigateTo(gwUrl(`/sandboxes/${selectedSandbox}/policy`));
    } catch (e) {
      showToast(`Failed to apply preset: ${(e as Error).message}`, "danger");
    }
  };

  if (loading) return <Spinner message="Loading preset..." />;
  if (error) return <ErrorAlert message={error} />;

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <div>
          <h4 class="mb-1 d-inline me-2">{meta.name || presetName}</h4>
        </div>
      </div>

      {meta.description && <p class="text-muted mb-3">{meta.description}</p>}

      <div class="card sg-card-themed mb-4">
        <div class="card-body py-2">
          <div class="d-flex align-items-center gap-2">
            <span class="text-muted small">Apply to:</span>
            <select
              class="form-select form-select-sm sg-mw-250"
              value={selectedSandbox}
              onChange={(e) => setSelectedSandbox((e.target as HTMLSelectElement).value)}
            >
              <option value="">Select a sandbox...</option>
              {sandboxes.map((sb) => (
                <option key={sb.name} value={sb.name}>
                  {sb.name}
                </option>
              ))}
            </select>
            <button
              class="btn btn-success btn-sm"
              disabled={!selectedSandbox}
              onClick={() => void applyToSandbox()}
            >
              <i class="bi bi-shield-plus me-1" />
              Apply
            </button>
          </div>
        </div>
      </div>

      <h6 class="text-muted mb-2">
        Network Rules <span class="badge text-bg-secondary ms-1">{ruleEntries.length}</span>
      </h6>

      {ruleEntries.length > 0 ? (
        <div class="table-responsive">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Host</th>
                <th>Port</th>
                <th>Protocol</th>
                <th>TLS</th>
                <th>L7 Rules</th>
              </tr>
            </thead>
            <tbody>
              {ruleEntries.flatMap((entry) =>
                entry.endpoints.map((ep, i) => (
                  <tr key={`${entry.key}-${i}`}>
                    {i === 0 && (
                      <td rowSpan={entry.endpoints.length}>
                        <strong>{entry.name}</strong>
                      </td>
                    )}
                    <td class="font-monospace">{ep.host}</td>
                    <td>{ep.port}</td>
                    <td>
                      {ep.protocol ? (
                        <span class="badge text-bg-secondary">{ep.protocol}</span>
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                    <td>
                      {ep.tls ? (
                        <span class="badge text-bg-info">{ep.tls}</span>
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                    <td>
                      {(ep.rules ?? []).length > 0 ? (
                        <span>
                          {ep.rules!.map((r, ri) => (
                            <span key={ri} class="badge endpoint-badge me-1">
                              {(r.allow?.method || "*") + " " + (r.allow?.path || "/*")}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div class="text-muted">No network rules defined.</div>
      )}
    </div>
  );
}
