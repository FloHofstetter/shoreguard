/** Tenants list page (island): CRUD + gateway/user membership. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch, type User } from "../lib/api";
import { formatDate } from "../lib/format";
import { showConfirm, showToast } from "../lib/notify";
import { EmptyState, ErrorAlert, Spinner } from "../lib/widgets";

interface Tenant {
  id: number;
  name: string;
  description?: string | null;
  gateway_count: number;
  user_count: number;
  created_at?: string | null;
}

interface TenantDetail {
  id: number;
  name: string;
  description?: string | null;
  gateways: string[];
  users: { id: number; email: string }[];
}

function TenantMembers({ tenantId, onChanged }: { tenantId: number; onChanged: () => void }) {
  const [detail, setDetail] = useState<TenantDetail | null>(null);
  const [gateways, setGateways] = useState<string[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [gwToAdd, setGwToAdd] = useState("");
  const [userToAdd, setUserToAdd] = useState("");

  const load = () => {
    apiFetch<TenantDetail>(`/api/tenants/${tenantId}`)
      .then(setDetail)
      .catch(() => setDetail(null));
  };
  useEffect(() => {
    load();
    apiFetch<{ items: { name: string }[] }>("/api/gateway/list")
      .then((r) => setGateways(r.items.map((g) => g.name)))
      .catch(() => undefined);
    apiFetch<User[]>("/api/users")
      .then(setUsers)
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);

  if (!detail) return <Spinner />;

  const addGw = async () => {
    if (!gwToAdd) return;
    try {
      await apiFetch(`/api/tenants/${tenantId}/gateways/${gwToAdd}`, { method: "PUT" });
      showToast("Gateway added", "success");
      setGwToAdd("");
      load();
      onChanged();
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };
  const removeGw = async (name: string) => {
    try {
      await apiFetch(`/api/tenants/${tenantId}/gateways/${name}`, { method: "DELETE" });
      load();
      onChanged();
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };
  const addUser = async () => {
    if (!userToAdd) return;
    try {
      await apiFetch(`/api/tenants/${tenantId}/users/${userToAdd}`, { method: "PUT" });
      showToast("User added", "success");
      setUserToAdd("");
      load();
      onChanged();
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };
  const removeUser = async (uid: number) => {
    try {
      await apiFetch(`/api/tenants/${tenantId}/users/${uid}`, { method: "DELETE" });
      load();
      onChanged();
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  const availableGw = gateways.filter((g) => !detail.gateways.includes(g));
  const memberIds = new Set(detail.users.map((u) => u.id));
  const availableUsers = users.filter((u) => !memberIds.has(u.id));

  return (
    <div class="p-2">
      <div class="row g-3">
        <div class="col-md-6">
          <div class="small text-muted mb-1">Gateways</div>
          <div class="d-flex flex-wrap gap-1 mb-2">
            {detail.gateways.length === 0 ? (
              <span class="text-muted small">none</span>
            ) : (
              detail.gateways.map((g) => (
                <span key={g} class="badge text-bg-secondary">
                  {g}
                  <button
                    type="button"
                    class="btn-close btn-close-white ms-1"
                    style={{ fontSize: "0.6em" }}
                    aria-label={`Remove ${g}`}
                    onClick={() => void removeGw(g)}
                  />
                </span>
              ))
            )}
          </div>
          <div class="d-flex gap-1">
            <select
              class="form-select form-select-sm"
              value={gwToAdd}
              onChange={(e) => setGwToAdd((e.target as HTMLSelectElement).value)}
            >
              <option value="">add gateway…</option>
              {availableGw.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
            <button class="btn btn-sm btn-outline-success" onClick={() => void addGw()}>
              Add
            </button>
          </div>
        </div>
        <div class="col-md-6">
          <div class="small text-muted mb-1">Users</div>
          <div class="d-flex flex-wrap gap-1 mb-2">
            {detail.users.length === 0 ? (
              <span class="text-muted small">none</span>
            ) : (
              detail.users.map((u) => (
                <span key={u.id} class="badge text-bg-secondary">
                  {u.email}
                  <button
                    type="button"
                    class="btn-close btn-close-white ms-1"
                    style={{ fontSize: "0.6em" }}
                    aria-label={`Remove ${u.email}`}
                    onClick={() => void removeUser(u.id)}
                  />
                </span>
              ))
            )}
          </div>
          <div class="d-flex gap-1">
            <select
              class="form-select form-select-sm"
              value={userToAdd}
              onChange={(e) => setUserToAdd((e.target as HTMLSelectElement).value)}
            >
              <option value="">add user…</option>
              {availableUsers.map((u) => (
                <option key={u.id} value={String(u.id)}>
                  {u.email}
                </option>
              ))}
            </select>
            <button class="btn btn-sm btn-outline-success" onClick={() => void addUser()}>
              Add
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function TenantsPage() {
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = () => {
    apiFetch<{ items: Tenant[] }>("/api/tenants")
      .then((r) => setTenants(r.items))
      .catch((e) => setError((e as Error).message));
  };
  useEffect(load, []);

  const create = async () => {
    if (!newName.trim()) return;
    try {
      await apiFetch("/api/tenants", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName.trim(), description: newDesc.trim() || null }),
      });
      showToast("Tenant created", "success");
      setNewName("");
      setNewDesc("");
      load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  const remove = async (t: Tenant) => {
    const ok = await showConfirm(`Delete tenant "${t.name}"? Members are unassigned.`, {
      icon: "trash",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/tenants/${t.id}`, { method: "DELETE" });
      showToast("Tenant deleted", "success");
      load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  if (error) return <ErrorAlert message={error} />;
  if (!tenants) return <Spinner />;

  return (
    <div>
      <div class="card sg-card-themed mb-4">
        <div class="card-body">
          <h6 class="mb-3">
            <i class="bi bi-diagram-3 me-2" />
            Create tenant
          </h6>
          <p class="small text-muted">
            A tenant scopes a non-admin user's gateway list, fleet view, and digest to the
            tenant's gateways. Admins and unassigned users always see the full fleet.
          </p>
          <div class="d-flex flex-wrap gap-2 align-items-center">
            <input
              class="form-control form-control-sm"
              style={{ maxWidth: "220px" }}
              placeholder="name"
              value={newName}
              onInput={(e) => setNewName((e.target as HTMLInputElement).value)}
            />
            <input
              class="form-control form-control-sm"
              style={{ maxWidth: "320px" }}
              placeholder="description (optional)"
              value={newDesc}
              onInput={(e) => setNewDesc((e.target as HTMLInputElement).value)}
            />
            <button class="btn btn-sm btn-success" onClick={() => void create()}>
              Create
            </button>
          </div>
        </div>
      </div>

      {tenants.length === 0 ? (
        <EmptyState
          icon="diagram-3"
          message="No tenants yet — create one to scope what each user sees."
        />
      ) : (
        <div class="card sg-card-themed">
          <div class="table-responsive">
            <table class="table table-sm align-middle mb-0">
              <thead>
                <tr>
                  <th>Name</th>
                  <th class="text-end">Gateways</th>
                  <th class="text-end">Users</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tenants.map((t) => (
                  <>
                    <tr key={t.id}>
                      <td class="fw-medium">
                        {t.name}
                        {t.description ? (
                          <div class="small text-muted">{t.description}</div>
                        ) : null}
                      </td>
                      <td class="text-end">{t.gateway_count}</td>
                      <td class="text-end">{t.user_count}</td>
                      <td class="small text-muted">
                        {t.created_at ? formatDate(t.created_at) : "—"}
                      </td>
                      <td class="text-end">
                        <button
                          class="btn btn-sm btn-outline-primary me-1"
                          onClick={() => setExpanded(expanded === t.id ? null : t.id)}
                        >
                          Manage
                        </button>
                        <button
                          class="btn btn-sm btn-outline-danger"
                          onClick={() => void remove(t)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                    {expanded === t.id && (
                      <tr key={`${t.id}-detail`}>
                        <td colSpan={5} class="bg-body-tertiary">
                          <TenantMembers tenantId={t.id} onChanged={load} />
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
