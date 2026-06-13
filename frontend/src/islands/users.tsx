/** Users & service principals list page (island). */

import { useEffect, useMemo, useState } from "preact/hooks";

import { apiFetch, type ServicePrincipal, type User } from "../lib/api";
import { daysUntil, formatDate, formatTimeAgo, roleBadge } from "../lib/format";
import { EmptyState } from "../lib/widgets";
import { showConfirm, showToast } from "../lib/notify";
import { Modal } from "../lib/Modal";
import { GatewayRolesModal, type RoleEntityType } from "./GatewayRolesModal";

type SortKey = "email" | "role" | "created_at";

function ExpiryBadge({ sp }: { sp: ServicePrincipal }) {
  if (!sp.expires_at) return <span class="text-muted">Never</span>;
  const daysLeft = daysUntil(sp.expires_at);
  if (daysLeft <= 0) return <span class="badge text-bg-danger">Expired</span>;
  const cls = daysLeft <= 30 ? "text-bg-warning" : "text-bg-success";
  return <span class={`badge ${cls}`}>{daysLeft}d left</span>;
}

function KeyModal({ name, value, onClose }: { name: string; value: string; onClose: () => void }) {
  const copy = () => {
    void navigator.clipboard
      .writeText(value)
      .then(() => showToast("Key copied to clipboard", "success"));
  };
  return (
    <Modal
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-key me-2" />
          API Key — {name}
        </span>
      }
    >
      <div class="alert alert-warning mb-3">
        <i class="bi bi-exclamation-triangle me-1" />
        This key is shown only once. Copy it now.
      </div>
      <div class="input-group">
        <input type="text" class="form-control font-monospace" value={value} readOnly />
        <button class="btn btn-outline-secondary" title="Copy" onClick={copy}>
          <i class="bi bi-clipboard" />
        </button>
      </div>
    </Modal>
  );
}

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [sps, setSps] = useState<ServicePrincipal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("email");
  const [sortAsc, setSortAsc] = useState(true);
  const [rolesModal, setRolesModal] = useState<{
    entityType: RoleEntityType;
    entityId: number;
    entityLabel: string;
  } | null>(null);
  const [keyModal, setKeyModal] = useState<{ name: string; value: string } | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [loadedUsers, loadedSps] = await Promise.all([
        apiFetch<User[]>(`/api/auth/users`),
        apiFetch<ServicePrincipal[]>(`/api/auth/service-principals`),
      ]);
      setUsers(loadedUsers);
      setSps(loadedSps);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const visibleUsers = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const filtered = needle
      ? users.filter(
          (u) => u.email.toLowerCase().includes(needle) || u.role.toLowerCase().includes(needle),
        )
      : users;
    const sorted = [...filtered].sort((a, b) => {
      const av = a[sortKey] ?? "";
      const bv = b[sortKey] ?? "";
      return av < bv ? -1 : av > bv ? 1 : 0;
    });
    return sortAsc ? sorted : sorted.reverse();
  }, [users, filter, sortKey, sortAsc]);

  const sortBy = (key: SortKey) => {
    if (key === sortKey) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const sortClass = (key: SortKey) =>
    `sg-sortable ${sortKey === key ? (sortAsc ? "sg-sort-asc" : "sg-sort-desc") : ""}`;

  const deleteUser = async (u: User) => {
    const confirmed = await showConfirm(`Delete user "${u.email}"?`, {
      icon: "trash",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!confirmed) return;
    try {
      await apiFetch(`/api/auth/users/${u.id}`, { method: "DELETE" });
      showToast(`User "${u.email}" deleted.`, "success");
      await load();
    } catch (e) {
      showToast(`Delete failed: ${(e as Error).message}`, "danger");
    }
  };

  const deleteSP = async (sp: ServicePrincipal) => {
    const confirmed = await showConfirm(`Delete service principal "${sp.name}"?`, {
      icon: "trash",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!confirmed) return;
    try {
      await apiFetch(`/api/auth/service-principals/${sp.id}`, { method: "DELETE" });
      showToast(`Service principal "${sp.name}" deleted.`, "success");
      await load();
    } catch (e) {
      showToast(`Delete failed: ${(e as Error).message}`, "danger");
    }
  };

  const rotateSP = async (sp: ServicePrincipal) => {
    const confirmed = await showConfirm(
      `Rotate the key for "${sp.name}"? The current key stops working immediately.`,
      { icon: "arrow-repeat", btnClass: "btn-warning", btnLabel: "Rotate" },
    );
    if (!confirmed) return;
    try {
      const result = await apiFetch<{ key: string }>(
        `/api/auth/service-principals/${sp.id}/rotate`,
        { method: "POST" },
      );
      setKeyModal({ name: sp.name, value: result.key });
      await load();
    } catch (e) {
      showToast(`Rotate failed: ${(e as Error).message}`, "danger");
    }
  };

  const closeRolesModal = () => {
    setRolesModal(null);
    void load();
  };

  if (loading) {
    return (
      <div class="text-center text-muted py-5">
        <div class="spinner-border spinner-border-sm me-2" />
        Loading...
      </div>
    );
  }
  if (error) {
    return <div class="alert alert-danger">{error}</div>;
  }

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">
          <i class="bi bi-people me-2" />
          Users
        </h5>
        <div class="btn-group btn-group-sm">
          <button
            class="btn btn-outline-secondary"
            onClick={() => void load()}
            title="Refresh"
            aria-label="Refresh"
          >
            <i class="bi bi-arrow-clockwise" />
          </button>
          <a href="/users/new" class="btn btn-outline-success">
            <i class="bi bi-plus-lg me-1" />
            Invite User
          </a>
        </div>
      </div>

      {users.length > 0 && (
        <div class="mb-2">
          <input
            type="text"
            class="form-control form-control-sm sg-mw-300"
            placeholder="Filter users..."
            value={filter}
            onInput={(e) => setFilter((e.target as HTMLInputElement).value)}
          />
        </div>
      )}

      {users.length > 0 ? (
        <div class="table-responsive mb-5">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th class={sortClass("email")} onClick={() => sortBy("email")}>
                  Email
                </th>
                <th class={sortClass("role")} onClick={() => sortBy("role")}>
                  Role
                </th>
                <th>Status</th>
                <th
                  class={`d-none d-md-table-cell ${sortClass("created_at")}`}
                  onClick={() => sortBy("created_at")}
                >
                  Created
                </th>
                <th class="text-end sg-w-60">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visibleUsers.map((u) => (
                <tr key={u.id}>
                  <td>
                    <strong>{u.email}</strong>
                    {u.oidc_provider && (
                      <span class="badge text-bg-info ms-1">{u.oidc_provider}</span>
                    )}
                  </td>
                  <td>
                    <span class={`badge ${roleBadge(u.role)}`}>{u.role}</span>
                  </td>
                  <td>
                    {u.pending_invite ? (
                      <span class="badge text-bg-info">Invited</span>
                    ) : (
                      <span class="badge text-bg-success">Active</span>
                    )}
                  </td>
                  <td class="d-none d-md-table-cell small text-muted">
                    {formatDate(u.created_at)}
                  </td>
                  <td class="text-end">
                    <button
                      class="btn btn-sm text-muted"
                      title="Gateway Roles"
                      onClick={() =>
                        setRolesModal({ entityType: "user", entityId: u.id, entityLabel: u.email })
                      }
                    >
                      <i class="bi bi-shield-lock" />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title="Delete"
                      onClick={() => void deleteUser(u)}
                    >
                      <i class="bi bi-trash3" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState icon="people" message="No users yet.">
          <a href="/users/new" class="btn btn-success btn-sm">
            <i class="bi bi-plus me-1" />
            Invite User
          </a>
        </EmptyState>
      )}

      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">
          <i class="bi bi-key me-2" />
          Service Principals
        </h5>
        <a href="/users/new-service-principal" class="btn btn-outline-success btn-sm">
          <i class="bi bi-plus-lg me-1" />
          New
        </a>
      </div>

      {sps.length > 0 ? (
        <div class="table-responsive">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th>Name</th>
                <th class="d-none d-lg-table-cell">Key</th>
                <th>Role</th>
                <th class="d-none d-md-table-cell">Expires</th>
                <th class="d-none d-md-table-cell">Last Used</th>
                <th class="text-end sg-w-100">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sps.map((sp) => (
                <tr key={sp.id}>
                  <td>
                    <strong>{sp.name}</strong>
                  </td>
                  <td class="d-none d-lg-table-cell">
                    <code class="small">{sp.key_prefix ? `${sp.key_prefix}...` : "(legacy)"}</code>
                  </td>
                  <td>
                    <span class={`badge ${roleBadge(sp.role)}`}>{sp.role}</span>
                  </td>
                  <td class="d-none d-md-table-cell small">
                    <ExpiryBadge sp={sp} />
                  </td>
                  <td class="d-none d-md-table-cell small text-muted">
                    {sp.last_used ? formatTimeAgo(sp.last_used) : "Never"}
                  </td>
                  <td class="text-end">
                    <button
                      class="btn btn-sm text-muted"
                      title="Rotate Key"
                      onClick={() => void rotateSP(sp)}
                    >
                      <i class="bi bi-arrow-repeat" />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title="Gateway Roles"
                      onClick={() =>
                        setRolesModal({ entityType: "sp", entityId: sp.id, entityLabel: sp.name })
                      }
                    >
                      <i class="bi bi-shield-lock" />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title="Delete"
                      onClick={() => void deleteSP(sp)}
                    >
                      <i class="bi bi-trash3" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState icon="key" message="No service principals yet.">
          <a href="/users/new-service-principal" class="btn btn-success btn-sm">
            <i class="bi bi-plus me-1" />
            Create Service Principal
          </a>
        </EmptyState>
      )}

      {rolesModal && <GatewayRolesModal {...rolesModal} onClose={closeRolesModal} />}
      {keyModal && (
        <KeyModal name={keyModal.name} value={keyModal.value} onClose={() => setKeyModal(null)} />
      )}
    </div>
  );
}
