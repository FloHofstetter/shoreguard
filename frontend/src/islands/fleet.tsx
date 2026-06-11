/** Fleet page (island): cross-gateway overview, policy drift, policy sync. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth, hasRole } from "../lib/auth";
import { showConfirm, showToast } from "../lib/notify";
import { EmptyState, ErrorAlert, Spinner } from "../lib/widgets";

interface FleetGateway {
  name: string;
  status: string;
  version: string | null;
  reachable: boolean;
  sandbox_count: number;
  sandboxes: Record<string, string>;
}

interface DriftItem {
  sandbox: string;
  hashes: Record<string, string>;
  drifted: boolean;
}

function statusBadge(status: string, reachable: boolean): string {
  if (!reachable) return "text-bg-danger";
  if (status === "connected" || status === "serving") return "text-bg-success";
  return "text-bg-secondary";
}

function shortHash(hash: string): string {
  return hash ? hash.slice(0, 10) : "—";
}

function DriftRow({
  item,
  isOperator,
  onSynced,
}: {
  item: DriftItem;
  isOperator: boolean;
  onSynced: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const gateways = Object.keys(item.hashes);

  const sync = async (source: string) => {
    const targets = gateways.filter((g) => g !== source);
    const confirmed = await showConfirm(
      `Push the policy of "${item.sandbox}" from ${source} to ${targets.join(", ")}? ` +
        "This overwrites the target policies (a new revision — revertable via history).",
      { icon: "arrow-left-right", btnClass: "btn-warning", btnLabel: "Sync policy" },
    );
    if (!confirmed) return;
    setBusy(true);
    try {
      const r = await apiFetch<{ synced: string[]; errors: Record<string, string> }>(
        `/api/fleet/policy-sync`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_gateway: source, sandbox: item.sandbox, targets }),
        },
      );
      const errs = Object.keys(r.errors);
      showToast(
        `Synced to ${r.synced.length} gateway(s)` +
          (errs.length ? `, failed: ${errs.join(", ")}` : "") +
          ".",
        errs.length ? "warning" : "success",
      );
      onSynced();
    } catch (e) {
      showToast((e as Error).message, "danger");
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr>
      <td>
        <strong>{item.sandbox}</strong>
      </td>
      <td>
        {item.drifted ? (
          <span class="badge text-bg-warning">drifted</span>
        ) : (
          <span class="badge text-bg-success">in sync</span>
        )}
      </td>
      <td class="font-monospace small">
        {gateways.map((gw) => (
          <div key={gw} class="d-flex align-items-center gap-2">
            <span class="text-muted">{gw}:</span>
            <span>{shortHash(item.hashes[gw])}</span>
            {isOperator && item.drifted && (
              <button
                class="btn btn-sm btn-outline-warning py-0"
                disabled={busy}
                title={`Use ${gw}'s policy as the source of truth`}
                onClick={() => void sync(gw)}
              >
                use as source
              </button>
            )}
          </div>
        ))}
      </td>
    </tr>
  );
}

export default function FleetPage() {
  const [gateways, setGateways] = useState<FleetGateway[] | null>(null);
  const [drift, setDrift] = useState<DriftItem[] | null>(null);
  const [error, setError] = useState("");
  const [isOperator, setIsOperator] = useState(false);

  const load = async () => {
    try {
      const [ov, dr] = await Promise.all([
        apiFetch<{ gateways: FleetGateway[] }>(`/api/fleet/overview`),
        apiFetch<{ items: DriftItem[] }>(`/api/fleet/policy-drift`),
      ]);
      setGateways(ov.gateways);
      setDrift(dr.items);
    } catch (e) {
      setError((e as Error).message);
      setGateways([]);
      setDrift([]);
    }
  };

  useEffect(() => {
    void (async () => {
      await ensureAuth();
      setIsOperator(hasRole("operator"));
      await load();
    })();
  }, []);

  if (gateways === null) return <Spinner message="Collecting fleet state..." />;
  if (error) return <ErrorAlert message={error} />;

  const versions = new Set(gateways.map((g) => g.version).filter(Boolean));

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">
          <i class="bi bi-diagram-3 me-2" />
          Fleet
        </h5>
        <button class="btn btn-sm btn-outline-secondary" onClick={() => void load()}>
          <i class="bi bi-arrow-clockwise me-1" />
          Refresh
        </button>
      </div>

      {versions.size > 1 && (
        <div class="alert alert-warning py-2">
          <i class="bi bi-exclamation-triangle me-1" />
          Gateways run different OpenShell versions — consider upgrading the stragglers.
        </div>
      )}

      {gateways.length === 0 ? (
        <EmptyState icon="diagram-3" message="No gateways registered yet." />
      ) : (
        <div class="table-responsive mb-4">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th>Gateway</th>
                <th>Status</th>
                <th>OpenShell</th>
                <th>Sandboxes</th>
              </tr>
            </thead>
            <tbody>
              {gateways.map((gw) => (
                <tr key={gw.name}>
                  <td>
                    <a href={`/gateways/${gw.name}`} class="text-decoration-none fw-medium">
                      {gw.name}
                    </a>
                  </td>
                  <td>
                    <span class={`badge ${statusBadge(gw.status, gw.reachable)}`}>
                      {gw.reachable ? gw.status : "unreachable"}
                    </span>
                  </td>
                  <td class="font-monospace small">{gw.version ?? "—"}</td>
                  <td>{gw.reachable ? gw.sandbox_count : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h6 class="mb-2">
        <i class="bi bi-shield-check me-2" />
        Policy drift{" "}
        <span class="text-muted small fw-normal">
          (same-named sandboxes on two or more gateways)
        </span>
      </h6>
      {drift !== null && drift.length === 0 ? (
        <div class="text-muted small">
          No sandbox name appears on more than one reachable gateway.
        </div>
      ) : (
        <div class="table-responsive">
          <table class="table table-sm align-middle">
            <thead>
              <tr>
                <th>Sandbox</th>
                <th>State</th>
                <th>Policy hashes</th>
              </tr>
            </thead>
            <tbody>
              {(drift ?? []).map((item) => (
                <DriftRow
                  key={item.sandbox}
                  item={item}
                  isOperator={isOperator}
                  onSynced={() => void load()}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
