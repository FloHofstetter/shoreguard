/** Gateway-scoped role override editor, shared by users / SPs / groups. */

import { useCallback, useEffect, useState } from "preact/hooks";

import { apiFetch, type GatewayRole, type GatewaySummary } from "../lib/api";
import { roleBadge } from "../lib/format";
import { showToast } from "../lib/notify";
import { Modal } from "../lib/Modal";

export type RoleEntityType = "user" | "sp" | "group";

function basePathFor(entityType: RoleEntityType, entityId: number): string {
  if (entityType === "user") return `/api/auth/users/${entityId}/gateway-roles`;
  if (entityType === "group") return `/api/auth/groups/${entityId}/gateway-roles`;
  return `/api/auth/service-principals/${entityId}/gateway-roles`;
}

export interface GatewayRolesModalProps {
  entityType: RoleEntityType;
  entityId: number;
  entityLabel: string;
  onClose: () => void;
}

export function GatewayRolesModal({
  entityType,
  entityId,
  entityLabel,
  onClose,
}: GatewayRolesModalProps) {
  const basePath = basePathFor(entityType, entityId);
  const [roles, setRoles] = useState<GatewayRole[] | null>(null);
  const [gateways, setGateways] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [newGw, setNewGw] = useState("");
  const [newRole, setNewRole] = useState("viewer");

  const load = useCallback(async () => {
    try {
      const [loadedRoles, gwResp] = await Promise.all([
        apiFetch<GatewayRole[]>(basePath),
        apiFetch<{ items?: GatewaySummary[] } | GatewaySummary[]>(`/api/gateway/list`),
      ]);
      const gws = Array.isArray(gwResp) ? gwResp : (gwResp.items ?? []);
      setRoles(loadedRoles);
      setGateways(gws.map((g) => g.name));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [basePath]);

  useEffect(() => {
    void load();
  }, [load]);

  const addOverride = async () => {
    const gw = newGw || available[0];
    if (!gw) return;
    try {
      await apiFetch(`${basePath}/${gw}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: newRole }),
      });
      showToast(`Gateway role set: ${newRole} on ${gw}`, "success");
      await load();
    } catch (e) {
      showToast(`Failed: ${(e as Error).message}`, "danger");
    }
  };

  const removeOverride = async (gw: string) => {
    try {
      await apiFetch(`${basePath}/${gw}`, { method: "DELETE" });
      showToast(`Gateway role removed for ${gw}`, "success");
      await load();
    } catch (e) {
      showToast(`Failed: ${(e as Error).message}`, "danger");
    }
  };

  const available = gateways.filter((n) => !(roles ?? []).some((r) => r.gateway_name === n));

  return (
    <Modal
      size="lg"
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-shield-lock me-2" />
          Gateway Roles: {entityLabel}
        </span>
      }
    >
      {error && <div class="alert alert-danger">{error}</div>}
      {roles === null && !error && (
        <div class="text-center text-muted py-3">
          <div class="spinner-border spinner-border-sm me-2" />
          Loading gateway roles...
        </div>
      )}
      {roles !== null && roles.length > 0 && (
        <div class="table-responsive mb-3">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th>Gateway</th>
                <th>Role</th>
                <th class="text-end sg-w-60" />
              </tr>
            </thead>
            <tbody>
              {roles.map((r) => (
                <tr key={r.gateway_name}>
                  <td>
                    <strong>{r.gateway_name}</strong>
                  </td>
                  <td>
                    <span class={`badge ${roleBadge(r.role)}`}>{r.role}</span>
                  </td>
                  <td class="text-end">
                    <button
                      class="btn btn-sm text-muted"
                      title="Remove override"
                      onClick={() => void removeOverride(r.gateway_name)}
                    >
                      <i class="bi bi-trash3" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {roles !== null && roles.length === 0 && (
        <p class="text-muted mb-3">
          No gateway-specific role overrides. The global role applies everywhere.
        </p>
      )}
      {roles !== null && available.length > 0 && (
        <div class="card sg-card-themed">
          <div class="card-body py-2">
            <div class="row g-2 align-items-end">
              <div class="col">
                <label class="form-label small text-muted mb-1">Gateway</label>
                <select
                  class="form-select form-select-sm"
                  value={newGw || available[0]}
                  onChange={(e) => setNewGw((e.target as HTMLSelectElement).value)}
                >
                  {available.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </div>
              <div class="col">
                <label class="form-label small text-muted mb-1">Role</label>
                <select
                  class="form-select form-select-sm"
                  value={newRole}
                  onChange={(e) => setNewRole((e.target as HTMLSelectElement).value)}
                >
                  <option value="admin">admin</option>
                  <option value="operator">operator</option>
                  <option value="viewer">viewer</option>
                </select>
              </div>
              <div class="col-auto">
                <button class="btn btn-sm btn-outline-success" onClick={() => void addOverride()}>
                  <i class="bi bi-plus-lg me-1" />
                  Add
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
