/** Invite-user and new-service-principal forms (islands). */

import { useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { showToast } from "../lib/notify";

function RoleOptions({ full }: { full?: boolean }) {
  return (
    <>
      <option value="viewer">Viewer — read-only access</option>
      <option value="operator">Operator — sandbox, policy, provider management</option>
      <option value="admin">
        {full ? "Admin — full access including gateway and user management" : "Admin — full access"}
      </option>
    </>
  );
}

function CopyField({ value }: { value: string }) {
  return (
    <div class="input-group input-group-sm">
      <input type="text" class="form-control font-monospace" value={value} readOnly />
      <button
        type="button"
        class="btn btn-outline-secondary"
        onClick={() => {
          void navigator.clipboard.writeText(value);
          showToast("Copied!", "success");
        }}
      >
        <i class="bi bi-clipboard" />
      </button>
    </div>
  );
}

export function UserNewForm() {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [inviteUrl, setInviteUrl] = useState("");

  const submit = async (e: Event) => {
    e.preventDefault();
    if (!email.trim()) {
      setError("Email is required.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const data = await apiFetch<{ invite_token: string }>(`/api/auth/users`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), role }),
      });
      setInviteUrl(`${window.location.origin}/invite?token=${data.invite_token}`);
      showToast(`Invite for "${email.trim()}" created.`, "success");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div class="card sg-card-themed">
      <div class="card-body">
        <form noValidate onSubmit={(e) => void submit(e)}>
          <div class="mb-3">
            <label class="form-label">Email</label>
            <input
              type="email"
              class="form-control"
              placeholder="user@example.com"
              autofocus
              required
              value={email}
              onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
            />
            <div class="form-text">The user will set their own password via the invite link.</div>
          </div>
          <div class="mb-3">
            <label class="form-label">Role</label>
            <select
              class="form-select"
              required
              value={role}
              onChange={(e) => setRole((e.target as HTMLSelectElement).value)}
            >
              <RoleOptions full />
            </select>
          </div>

          {error && (
            <div class="text-danger small mb-3">
              <i class="bi bi-x-circle me-1" />
              <span>{error}</span>
            </div>
          )}

          {inviteUrl && (
            <div class="alert alert-success small py-2 mb-2">
              <p class="mb-1">
                <i class="bi bi-check-circle me-1" />
                Invite created! Share this link with the user:
              </p>
              <CopyField value={inviteUrl} />
            </div>
          )}

          <div class="d-flex gap-2">
            {!inviteUrl && (
              <button type="submit" class="btn btn-success" disabled={loading}>
                <i class="bi bi-send me-1" />
                Create Invite
              </button>
            )}
            {inviteUrl ? (
              <a href="/users" class="btn btn-outline-secondary btn-sm">
                <i class="bi bi-arrow-left me-1" />
                Back to Users
              </a>
            ) : (
              <a href="/users" class="btn btn-outline-secondary">
                Cancel
              </a>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

export function SpNewForm() {
  const [name, setName] = useState("");
  const [role, setRole] = useState("viewer");
  const [expiresDate, setExpiresDate] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [spKey, setSpKey] = useState("");

  const submit = async (e: Event) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const payload: Record<string, unknown> = { name: name.trim(), role };
      if (expiresDate) {
        payload.expires_at = new Date(`${expiresDate}T23:59:59Z`).toISOString();
      }
      const data = await apiFetch<{ key: string }>(`/api/auth/service-principals`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setSpKey(data.key);
      showToast(`Service principal "${name.trim()}" created.`, "success");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div class="card sg-card-themed">
      <div class="card-body">
        <form noValidate onSubmit={(e) => void submit(e)}>
          <div class="mb-3">
            <label class="form-label">Name</label>
            <input
              type="text"
              class="form-control"
              placeholder="terraform-ci"
              autofocus
              required
              value={name}
              onInput={(e) => setName((e.target as HTMLInputElement).value)}
            />
            <div class="form-text">A label to identify this service principal.</div>
          </div>
          <div class="mb-3">
            <label class="form-label">Role</label>
            <select
              class="form-select"
              required
              value={role}
              onChange={(e) => setRole((e.target as HTMLSelectElement).value)}
            >
              <RoleOptions />
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">Expires</label>
            <input
              type="date"
              class="form-control"
              value={expiresDate}
              onInput={(e) => setExpiresDate((e.target as HTMLInputElement).value)}
            />
            <div class="form-text">Leave empty for a non-expiring key.</div>
          </div>

          {error && (
            <div class="text-danger small mb-3">
              <i class="bi bi-x-circle me-1" />
              <span>{error}</span>
            </div>
          )}

          {spKey && (
            <div class="alert alert-success small py-2 mb-2">
              <p class="mb-1">
                <i class="bi bi-check-circle me-1" />
                Key created!
              </p>
              <span class="badge text-bg-warning mb-2">
                <i class="bi bi-exclamation-triangle me-1" />
                Show once — save this key now
              </span>
              <CopyField value={spKey} />
            </div>
          )}

          <div class="d-flex gap-2">
            {!spKey && (
              <button type="submit" class="btn btn-success" disabled={loading}>
                <i class="bi bi-plus me-1" />
                Create
              </button>
            )}
            {spKey ? (
              <a href="/users" class="btn btn-outline-secondary btn-sm">
                <i class="bi bi-arrow-left me-1" />
                Back to Users
              </a>
            ) : (
              <a href="/users" class="btn btn-outline-secondary">
                Cancel
              </a>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

export default UserNewForm;
