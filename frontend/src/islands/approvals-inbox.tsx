/** Approval inbox (island): aggregated pending policy approvals across the fleet. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth } from "../lib/auth";
import { showToast } from "../lib/notify";
import { ErrorAlert, Spinner } from "../lib/widgets";

interface GatewayItem {
  name: string;
  status?: string;
}

interface SbRaw {
  name: string;
  phase?: string;
}

interface Chunk {
  id: string;
  rule_name?: string;
  rationale?: string;
  security_notes?: string;
  confidence?: number;
  hit_count?: number;
  binary?: string;
  proposed_rule?: { endpoints?: { host?: string; port?: number }[] };
  narrowness?: { verdict: string; reasons?: string[] };
}

interface SandboxChunks {
  gateway: string;
  sandbox: string;
  chunks: Chunk[];
}

export interface InboxItem extends Chunk {
  gateway: string;
  sandbox: string;
}

/**
 * Flatten per-sandbox pending chunks into one prioritised inbox list:
 * security-flagged first, then by hit count, then by confidence.
 *
 * @param groups - Pending chunks grouped by (gateway, sandbox).
 * @returns One flat, sorted row per pending chunk.
 */
export function flattenInbox(groups: SandboxChunks[]): InboxItem[] {
  const items: InboxItem[] = [];
  for (const g of groups) {
    for (const c of g.chunks) items.push({ ...c, gateway: g.gateway, sandbox: g.sandbox });
  }
  return items.sort(
    (a, b) =>
      Number(!!b.security_notes) - Number(!!a.security_notes) ||
      (b.hit_count ?? 0) - (a.hit_count ?? 0) ||
      (b.confidence ?? 0) - (a.confidence ?? 0),
  );
}

function endpointsOf(it: InboxItem): string {
  return (it.proposed_rule?.endpoints ?? [])
    .map((e) => `${e.host ?? "*"}${e.port ? ":" + e.port : ""}`)
    .join(", ");
}

export default function ApprovalsInboxPage() {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = async () => {
    try {
      const gwData = await apiFetch<{ items?: GatewayItem[] }>(`/api/gateway/list`).catch(
        () => null,
      );
      const gws = gwData?.items ?? [];
      const groups = (
        await Promise.all(
          gws.map(async (g): Promise<SandboxChunks[]> => {
            try {
              const sbs = await apiFetch<SbRaw[] | { items?: SbRaw[] }>(
                `/api/gateways/${g.name}/sandboxes`,
              );
              const list = Array.isArray(sbs) ? sbs : (sbs?.items ?? []);
              return await Promise.all(
                list.slice(0, 40).map(async (s) => {
                  const chunks = await apiFetch<Chunk[]>(
                    `/api/gateways/${g.name}/sandboxes/${s.name}/approvals/pending`,
                  ).catch(() => []);
                  return {
                    gateway: g.name,
                    sandbox: s.name,
                    chunks: Array.isArray(chunks) ? chunks : [],
                  };
                }),
              );
            } catch {
              return [];
            }
          }),
        )
      ).flat();
      setItems(flattenInbox(groups));
    } catch (e) {
      setError((e as Error).message);
      setItems([]);
    }
  };

  useEffect(() => {
    void (async () => {
      await ensureAuth();
      await load();
    })();
  }, []);

  const act = async (it: InboxItem, decision: "approve" | "reject") => {
    const key = `${it.gateway}/${it.sandbox}/${it.id}`;
    setBusy(key);
    try {
      const res = await apiFetch<{ status?: string; votes?: number; needed?: number }>(
        `/api/gateways/${it.gateway}/sandboxes/${it.sandbox}/approvals/${it.id}/${decision}`,
        { method: "POST" },
      );
      if (res?.status === "pending") {
        const remaining = Math.max(0, (res.needed ?? 0) - (res.votes ?? 0));
        showToast(`Vote cast — ${res.votes}/${res.needed} approvals (${remaining} more).`, "info");
      } else {
        showToast(
          decision === "approve" ? "Approved." : "Rejected.",
          decision === "approve" ? "success" : "warning",
        );
      }
      await load();
    } catch (e) {
      showToast(`${decision} failed: ${(e as Error).message}`, "danger");
    } finally {
      setBusy("");
    }
  };

  if (items === null) return <Spinner message="Collecting approvals across your fleet..." />;
  if (error) return <ErrorAlert message={error} />;

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">
          <i class="bi bi-clipboard-check me-2" />
          Approval inbox{" "}
          <span class="text-muted small fw-normal">
            {items.length > 0 ? `${items.length} pending across all gateways` : "all gateways"}
          </span>
        </h5>
        <button class="btn btn-sm btn-outline-secondary" onClick={() => void load()}>
          <i class="bi bi-arrow-clockwise me-1" />
          Refresh
        </button>
      </div>

      {items.length === 0 ? (
        <div class="text-center text-muted py-5">
          <i class="bi bi-check2-circle fs-1 d-block mb-3 text-success" />
          <p class="mb-0">No pending approvals — your agents are within policy.</p>
        </div>
      ) : (
        <div class="vstack gap-2">
          {items.map((it) => {
            const key = `${it.gateway}/${it.sandbox}/${it.id}`;
            const flagged = !!it.security_notes;
            const endpoints = endpointsOf(it);
            return (
              <div key={key} class={`card sg-card-themed ${flagged ? "border-warning" : ""}`}>
                <div class="card-body py-2">
                  <div class="d-flex justify-content-between align-items-start gap-3">
                    <div style={{ minWidth: 0 }}>
                      <div class="d-flex align-items-center gap-2 flex-wrap">
                        <span class="fw-medium">{it.rule_name ?? it.id}</span>
                        {flagged && (
                          <span class="badge text-bg-warning">
                            <i class="bi bi-shield-exclamation me-1" />
                            security
                          </span>
                        )}
                        {it.narrowness?.verdict === "over_broad" && (
                          <span
                            class="badge text-bg-warning"
                            title={(it.narrowness.reasons ?? []).join("; ")}
                          >
                            <i class="bi bi-arrows-angle-expand me-1" />
                            over-broad
                          </span>
                        )}
                        {typeof it.confidence === "number" && (
                          <span class="badge text-bg-secondary">
                            {Math.round(it.confidence * 100)}%
                          </span>
                        )}
                        {typeof it.hit_count === "number" && it.hit_count > 0 && (
                          <span class="text-muted small">seen ×{it.hit_count}</span>
                        )}
                      </div>
                      <div class="small text-muted mt-1">
                        <a
                          href={`/gateways/${it.gateway}/sandboxes/${it.sandbox}/approvals`}
                          class="text-decoration-none fw-medium"
                        >
                          {it.sandbox}
                        </a>
                        <span class="mx-1">·</span>
                        {it.gateway}
                        {it.binary && (
                          <>
                            <span class="mx-1">·</span>
                            <span class="font-monospace">{it.binary}</span>
                          </>
                        )}
                        {endpoints && (
                          <>
                            <span class="mx-1">·</span>
                            {endpoints}
                          </>
                        )}
                      </div>
                      {it.rationale && <div class="small mt-1">{it.rationale}</div>}
                    </div>
                    <div class="d-flex gap-2 flex-shrink-0">
                      <button
                        class="btn btn-sm btn-success"
                        title="Approve"
                        disabled={busy === key}
                        onClick={() => void act(it, "approve")}
                      >
                        <i class="bi bi-check-lg" />
                      </button>
                      <button
                        class="btn btn-sm btn-outline-danger"
                        title="Reject"
                        disabled={busy === key}
                        onClick={() => void act(it, "reject")}
                      >
                        <i class="bi bi-x-lg" />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
