/** Groups list page (island): CRUD, members, gateway-role overrides. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch, type User } from "../lib/api";
import { formatDate, roleBadge } from "../lib/format";
import { Modal } from "../lib/Modal";
import { showConfirm, showToast } from "../lib/notify";
import { useSortableTable } from "../lib/table";
import { EmptyState, ErrorAlert, Spinner } from "../lib/widgets";
import { GatewayRolesModal } from "./GatewayRolesModal";

interface Group extends Record<string, unknown> {
  id: number;
  name: string;
  role: string;
  description?: string | null;
  member_count: number;
  created_at?: string | null;
}

interface GroupDetail extends Group {
  members: User[];
}

function RoleSelect({ value, onChange }: { value: string; onChange: (role: string) => void }) {
  return (
    <select
      class="form-select form-select-sm bg-dark text-light border-secondary"
      value={value}
      onChange={(e) => onChange((e.target as HTMLSelectElement).value)}
    >
      <option value="admin">admin</option>
      <option value="operator">operator</option>
      <option value="viewer">viewer</option>
    </select>
  );
}

function EditGroupModal({ group, onSaved, onClose }: {
  group: Group;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(group.name);
  const [role, setRole] = useState(group.role);
  const [desc, setDesc] = useState(group.description ?? "");
  const [error, setError] = useState("");

  const save = async () => {
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    setError("");
    try {
      await apiFetch(`/api/auth/groups/${group.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), role, description: desc.trim() || null }),
      });
      showToast("Group updated.", "success");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Modal
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-pencil me-2" />
          Edit Group
        </span>
      }
      footer={
        <button class="btn btn-success" onClick={() => void save()}>
          Save
        </button>
      }
    >
      {error && <div class="alert alert-danger py-1 small">{error}</div>}
      <div class="mb-3">
        <label class="form-label small">Name</label>
        <input
          type="text"
          class="form-control form-control-sm bg-dark text-light border-secondary"
          value={name}
          onInput={(e) => setName((e.target as HTMLInputElement).value)}
        />
      </div>
      <div class="mb-3">
        <label class="form-label small">Global Role</label>
        <RoleSelect value={role} onChange={setRole} />
      </div>
      <div class="mb-3">
        <label class="form-label small">Description</label>
        <input
          type="text"
          class="form-control form-control-sm bg-dark text-light border-secondary"
          value={desc}
          onInput={(e) => setDesc((e.target as HTMLInputElement).value)}
        />
      </div>
    </Modal>
  );
}

function MembersModal({ group, onChanged, onClose }: {
  group: Group;
  onChanged: () => void;
  onClose: () => void;
}) {
  const [members, setMembers] = useState<User[] | null>(null);
  const [available, setAvailable] = useState<User[]>([]);
  const [selectedUser, setSelectedUser] = useState("");

  const load = async () => {
    try {
      const [detail, users] = await Promise.all([
        apiFetch<GroupDetail>(`/api/auth/groups/${group.id}`),
        apiFetch<User[]>(`/api/auth/users`),
      ]);
      setMembers(detail.members);
      const memberIds = new Set(detail.members.map((m) => m.id));
      setAvailable(users.filter((u) => !memberIds.has(u.id)));
    } catch (e) {
      showToast(`Failed to load members: ${(e as Error).message}`, "danger");
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group.id]);

  const addMember = async () => {
    const userId = parseInt(selectedUser || String(available[0]?.id ?? ""), 10);
    if (!userId) return;
    try {
      await apiFetch(`/api/auth/groups/${group.id}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId }),
      });
      showToast("Member added.", "success");
      await load();
      onChanged();
    } catch (e) {
      showToast(`Failed: ${(e as Error).message}`, "danger");
    }
  };

  const removeMember = async (userId: number) => {
    try {
      await apiFetch(`/api/auth/groups/${group.id}/members/${userId}`, { method: "DELETE" });
      showToast("Member removed.", "success");
      await load();
      onChanged();
    } catch (e) {
      showToast(`Failed: ${(e as Error).message}`, "danger");
    }
  };

  return (
    <Modal
      size="lg"
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-people me-2" />
          Members: {group.name}
        </span>
      }
    >
      {members === null && (
        <div class="text-center text-muted py-3">
          <div class="spinner-border spinner-border-sm me-2" />
          Loading...
        </div>
      )}
      {members !== null && members.length > 0 && (
        <div class="table-responsive mb-3">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th>Email</th>
                <th>Global Role</th>
                <th class="text-end sg-w-60" />
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id}>
                  <td>{m.email}</td>
                  <td>
                    <span class={`badge ${roleBadge(m.role)}`}>{m.role}</span>
                  </td>
                  <td class="text-end">
                    <button
                      class="btn btn-sm text-muted"
                      title="Remove"
                      onClick={() => void removeMember(m.id)}
                    >
                      <i class="bi bi-x-lg" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {members !== null && members.length === 0 && <p class="text-muted mb-3">No members yet.</p>}
      {members !== null && available.length > 0 && (
        <div class="card bg-dark border-secondary">
          <div class="card-body py-2">
            <div class="row g-2 align-items-end">
              <div class="col">
                <label class="form-label small text-muted mb-1">Add User</label>
                <select
                  class="form-select form-select-sm bg-dark text-light border-secondary"
                  value={selectedUser || String(available[0]?.id ?? "")}
                  onChange={(e) => setSelectedUser((e.target as HTMLSelectElement).value)}
                >
                  {available.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.email}
                    </option>
                  ))}
                </select>
              </div>
              <div class="col-auto">
                <button class="btn btn-sm btn-outline-success" onClick={() => void addMember()}>
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

export default function GroupsPage() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const table = useSortableTable<Group>("name");

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newRole, setNewRole] = useState("viewer");
  const [newDesc, setNewDesc] = useState("");
  const [createError, setCreateError] = useState("");

  const [editGroup, setEditGroup] = useState<Group | null>(null);
  const [membersGroup, setMembersGroup] = useState<Group | null>(null);
  const [rolesGroup, setRolesGroup] = useState<Group | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setGroups(await apiFetch<Group[]>(`/api/auth/groups`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const createGroup = async () => {
    if (!newName.trim()) {
      setCreateError("Name is required.");
      return;
    }
    setCreateError("");
    try {
      await apiFetch(`/api/auth/groups`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newName.trim(),
          role: newRole,
          description: newDesc.trim() || null,
        }),
      });
      showToast(`Group "${newName.trim()}" created.`, "success");
      setNewName("");
      setNewRole("viewer");
      setNewDesc("");
      setShowCreate(false);
      await load();
    } catch (e) {
      setCreateError((e as Error).message);
    }
  };

  const deleteGroup = async (g: Group) => {
    const confirmed = await showConfirm(
      `Delete group "${g.name}"? All memberships and gateway roles will be removed.`,
      { icon: "trash", iconColor: "text-danger", btnClass: "btn-danger", btnLabel: "Delete" },
    );
    if (!confirmed) return;
    try {
      await apiFetch(`/api/auth/groups/${g.id}`, { method: "DELETE" });
      showToast(`Group "${g.name}" deleted.`, "success");
      await load();
    } catch (e) {
      showToast(`Delete failed: ${(e as Error).message}`, "danger");
    }
  };

  if (loading) return <Spinner />;
  if (error) return <ErrorAlert message={error} />;

  const visible = table.view(groups, "name", "description", "role");

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">
          <i class="bi bi-collection me-2" />
          Groups
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
          <button class="btn btn-outline-success" onClick={() => setShowCreate(!showCreate)}>
            <i class="bi bi-plus-lg me-1" />
            New Group
          </button>
        </div>
      </div>

      {showCreate && (
        <div class="card bg-dark border-secondary mb-3">
          <div class="card-body">
            {createError && <div class="alert alert-danger py-1 small">{createError}</div>}
            <div class="row g-2 align-items-end">
              <div class="col-md-4">
                <label class="form-label small text-muted mb-1">Name</label>
                <input
                  type="text"
                  class="form-control form-control-sm bg-dark text-light border-secondary"
                  placeholder="e.g. dev-team"
                  value={newName}
                  onInput={(e) => setNewName((e.target as HTMLInputElement).value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void createGroup();
                  }}
                />
              </div>
              <div class="col-md-2">
                <label class="form-label small text-muted mb-1">Global Role</label>
                <RoleSelect value={newRole} onChange={setNewRole} />
              </div>
              <div class="col-md-4">
                <label class="form-label small text-muted mb-1">Description</label>
                <input
                  type="text"
                  class="form-control form-control-sm bg-dark text-light border-secondary"
                  placeholder="Optional"
                  value={newDesc}
                  onInput={(e) => setNewDesc((e.target as HTMLInputElement).value)}
                />
              </div>
              <div class="col-md-2">
                <button class="btn btn-sm btn-success w-100" onClick={() => void createGroup()}>
                  <i class="bi bi-plus-lg me-1" />
                  Create
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {groups.length > 0 && (
        <div class="mb-2">
          <input
            type="text"
            class="form-control form-control-sm sg-mw-300"
            placeholder="Filter groups..."
            value={table.filterText}
            onInput={(e) => table.setFilterText((e.target as HTMLInputElement).value)}
          />
        </div>
      )}

      {groups.length > 0 ? (
        <div class="table-responsive">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th class={table.sortClass("name")} onClick={() => table.sortBy("name")}>
                  Name
                </th>
                <th class={table.sortClass("role")} onClick={() => table.sortBy("role")}>
                  Global Role
                </th>
                <th
                  class={table.sortClass("member_count")}
                  onClick={() => table.sortBy("member_count")}
                >
                  Members
                </th>
                <th class="d-none d-md-table-cell">Description</th>
                <th
                  class={`d-none d-md-table-cell ${table.sortClass("created_at")}`}
                  onClick={() => table.sortBy("created_at")}
                >
                  Created
                </th>
                <th class="text-end sg-w-120">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((g) => (
                <tr key={g.id}>
                  <td>
                    <strong>{g.name}</strong>
                  </td>
                  <td>
                    <span class={`badge ${roleBadge(g.role)}`}>{g.role}</span>
                  </td>
                  <td>
                    <span class="badge text-bg-info">{g.member_count}</span>
                  </td>
                  <td class="d-none d-md-table-cell small text-muted">{g.description || "—"}</td>
                  <td class="d-none d-md-table-cell small text-muted">
                    {formatDate(g.created_at)}
                  </td>
                  <td class="text-end">
                    <button
                      class="btn btn-sm text-muted"
                      title="Members"
                      onClick={() => setMembersGroup(g)}
                    >
                      <i class="bi bi-people" />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title="Gateway Roles"
                      onClick={() => setRolesGroup(g)}
                    >
                      <i class="bi bi-shield-lock" />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title="Edit"
                      onClick={() => setEditGroup(g)}
                    >
                      <i class="bi bi-pencil" />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title="Delete"
                      onClick={() => void deleteGroup(g)}
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
        <EmptyState icon="collection" message="No groups yet.">
          <button class="btn btn-success btn-sm" onClick={() => setShowCreate(true)}>
            <i class="bi bi-plus me-1" />
            Create Group
          </button>
        </EmptyState>
      )}

      {editGroup && (
        <EditGroupModal
          group={editGroup}
          onSaved={() => {
            setEditGroup(null);
            void load();
          }}
          onClose={() => setEditGroup(null)}
        />
      )}
      {membersGroup && (
        <MembersModal
          group={membersGroup}
          onChanged={() => void load()}
          onClose={() => setMembersGroup(null)}
        />
      )}
      {rolesGroup && (
        <GatewayRolesModal
          entityType="group"
          entityId={rolesGroup.id}
          entityLabel={rolesGroup.name}
          onClose={() => {
            setRolesGroup(null);
            void load();
          }}
        />
      )}
    </div>
  );
}
