/** Personal settings: passkeys and push-enabled devices. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { showConfirm, showToast } from "../lib/notify";
import { currentSubscription, disablePush, enablePush, pushSupported } from "../lib/push";
import { passkeysSupported, registerPasskey } from "../lib/webauthn";

interface Passkey {
  id: number;
  name: string;
  created_at: string | null;
  last_used: string | null;
}

interface PushDevice {
  id: number;
  endpoint: string;
  user_agent: string | null;
  created_at: string | null;
}

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

function PasskeySection() {
  const [keys, setKeys] = useState<Passkey[] | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setKeys(await apiFetch<Passkey[]>(`/api/auth/passkeys`));
    } catch {
      // 404 = feature disabled; 400 = no real user session (--no-auth).
      setUnavailable(true);
      setKeys([]);
    }
  };
  useEffect(() => {
    void load();
  }, []);

  const add = async () => {
    setBusy(true);
    try {
      await registerPasskey(name.trim() || "passkey");
      showToast("Passkey registered.", "success");
      setName("");
      await load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (key: Passkey) => {
    const confirmed = await showConfirm(`Delete passkey "${key.name}"?`, {
      icon: "trash",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!confirmed) return;
    try {
      await apiFetch(`/api/auth/passkeys/${key.id}`, { method: "DELETE" });
      showToast("Passkey deleted.", "success");
      await load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  return (
    <div class="card sg-card-themed mb-3">
      <div class="card-body">
        <h6 class="mb-3">
          <i class="bi bi-fingerprint me-2" />
          Passkeys
        </h6>
        {unavailable ? (
          <div class="text-muted small">
            Passkeys are unavailable — they need an enabled feature flag and a real user
            session (not <code>--no-auth</code>).
          </div>
        ) : keys === null ? (
          <div class="text-muted small">Loading…</div>
        ) : (
          <>
            {keys.length === 0 ? (
              <div class="text-muted small mb-3">
                No passkeys yet. Register one to sign in without a password — works great
                with the phone's screen lock.
              </div>
            ) : (
              <div class="table-responsive">
                <table class="table table-sm align-middle">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th class="d-none d-md-table-cell">Registered</th>
                    <th class="d-none d-md-table-cell">Last used</th>
                    <th class="text-end">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {keys.map((k) => (
                    <tr key={k.id}>
                      <td>{k.name}</td>
                      <td class="d-none d-md-table-cell text-muted small">{fmt(k.created_at)}</td>
                      <td class="d-none d-md-table-cell text-muted small">{fmt(k.last_used)}</td>
                      <td class="text-end">
                        <button
                          class="btn btn-sm btn-outline-danger"
                          title="Delete passkey"
                          onClick={() => void remove(k)}
                        >
                          <i class="bi bi-trash" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
                </table>
              </div>
            )}
            {passkeysSupported() ? (
              <div class="d-flex gap-2">
                <input
                  type="text"
                  class="form-control form-control-sm sg-mw-300"
                  placeholder="Device name (e.g. Pixel 9)"
                  value={name}
                  onInput={(e) => setName((e.target as HTMLInputElement).value)}
                />
                <button class="btn btn-sm btn-outline-primary" disabled={busy} onClick={() => void add()}>
                  <i class="bi bi-plus-lg me-1" />
                  Add passkey
                </button>
              </div>
            ) : (
              <div class="text-muted small">
                This browser cannot register passkeys here — passkeys need HTTPS (or
                localhost).
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function PushDevicesSection() {
  const [devices, setDevices] = useState<PushDevice[] | null>(null);
  const [thisDevice, setThisDevice] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setDevices(await apiFetch<PushDevice[]>(`/api/push/subscriptions`));
    } catch {
      setDevices([]);
    }
    currentSubscription()
      .then((sub) => setThisDevice(sub !== null))
      .catch(() => setThisDevice(false));
  };
  useEffect(() => {
    void load();
  }, []);

  const toggle = async () => {
    setBusy(true);
    try {
      if (thisDevice) {
        await disablePush();
        showToast("Push disabled on this device.", "info");
      } else {
        await enablePush();
        showToast("Push enabled on this device.", "success");
      }
      await load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div class="card sg-card-themed mb-3">
      <div class="card-body">
        <h6 class="mb-3">
          <i class="bi bi-bell me-2" />
          Push devices
        </h6>
        {devices === null ? (
          <div class="text-muted small">Loading…</div>
        ) : devices.length === 0 ? (
          <div class="text-muted small mb-3">
            No devices receive push notifications yet. Pair with a <code>webpush</code>{" "}
            webhook to choose which events arrive.
          </div>
        ) : (
          <ul class="list-unstyled small mb-3">
            {devices.map((d) => (
              <li key={d.id} class="mb-1">
                <i class="bi bi-phone me-1" />
                <span class="font-monospace">{d.endpoint}</span>
                <span class="text-muted ms-2">{fmt(d.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
        {pushSupported() ? (
          <button class="btn btn-sm btn-outline-primary" disabled={busy} onClick={() => void toggle()}>
            <i class={`bi ${thisDevice ? "bi-bell-slash" : "bi-bell"} me-1`} />
            {thisDevice ? "Disable push on this device" : "Enable push on this device"}
          </button>
        ) : (
          <div class="text-muted small">
            Push needs HTTPS (or localhost) and a modern browser.
          </div>
        )}
      </div>
    </div>
  );
}

interface SessionItem {
  id: number;
  kind: string;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
  last_seen_at: string;
  current: boolean;
}

const KIND_META: Record<string, { icon: string; label: string }> = {
  password: { icon: "key", label: "Password" },
  passkey: { icon: "fingerprint", label: "Passkey" },
  oidc: { icon: "box-arrow-in-right", label: "SSO" },
  "device-link": { icon: "qr-code", label: "Phone (QR handoff)" },
  invite: { icon: "envelope", label: "Invite" },
  setup: { icon: "person-gear", label: "Setup" },
  register: { icon: "person-plus", label: "Self-registration" },
};

function SessionsSection() {
  const [sessions, setSessions] = useState<SessionItem[] | null>(null);
  const [tracking, setTracking] = useState<boolean | null>(null);

  const load = async () => {
    const check = await fetch("/api/auth/check").then((r) => r.json()).catch(() => ({}));
    if (!check.session_tracking) {
      setTracking(false);
      return;
    }
    setTracking(true);
    try {
      const r = await apiFetch<{ sessions: SessionItem[] }>(`/api/auth/sessions`);
      setSessions(r?.sessions ?? []);
    } catch {
      setSessions([]);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const revoke = async (s: SessionItem) => {
    if (s.current) {
      if (!(await showConfirm("Sign out this device? You will be returned to the login page.")))
        return;
    }
    try {
      await apiFetch(`/api/auth/sessions/${s.id}`, { method: "DELETE" });
      if (s.current) {
        window.location.href = "/login";
        return;
      }
      setSessions((cur) => (cur ?? []).filter((x) => x.id !== s.id));
      showToast("Session revoked — that device is now signed out.", "success");
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  const revokeOthers = async () => {
    if (!(await showConfirm("Sign out all other devices?"))) return;
    try {
      const r = await apiFetch<{ revoked: number }>(`/api/auth/sessions/revoke-others`, {
        method: "POST",
      });
      showToast(`Signed out ${r?.revoked ?? 0} other device(s).`, "success");
      void load();
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  if (tracking === false) return null;
  const others = (sessions ?? []).filter((s) => !s.current).length;

  return (
    <div class="card mb-3">
      <div class="card-body">
        <h6 class="mb-3 d-flex justify-content-between align-items-center">
          <span>
            <i class="bi bi-display me-2" />
            Active sessions
          </span>
          {others > 0 && (
            <button class="btn btn-sm btn-outline-danger" onClick={() => void revokeOthers()}>
              <i class="bi bi-box-arrow-right me-1" />
              Sign out other devices
            </button>
          )}
        </h6>
        {sessions === null ? (
          <div class="text-muted small">
            <span class="spinner-border spinner-border-sm me-1" />
            Loading…
          </div>
        ) : sessions.length === 0 ? (
          <div class="text-muted small">No active sessions.</div>
        ) : (
          <div class="list-group list-group-flush">
            {sessions.map((s) => {
              const meta = KIND_META[s.kind] ?? { icon: "display", label: s.kind };
              return (
                <div
                  key={s.id}
                  class="list-group-item d-flex justify-content-between align-items-center px-0"
                >
                  <div style={{ minWidth: 0 }}>
                    <div>
                      <i class={`bi bi-${meta.icon} me-2`} />
                      {meta.label}
                      {s.current && <span class="badge bg-success ms-2">This device</span>}
                    </div>
                    <div class="small text-muted text-truncate">
                      {s.ip ?? "unknown IP"}
                      {s.user_agent ? ` · ${s.user_agent}` : ""}
                    </div>
                    <div class="small text-muted">Last seen {fmt(s.last_seen_at)}</div>
                  </div>
                  <button
                    class={`btn btn-sm ${s.current ? "btn-outline-secondary" : "btn-outline-danger"}`}
                    onClick={() => void revoke(s)}
                  >
                    {s.current ? "Sign out" : "Revoke"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ProfilePage() {
  return (
    <div>
      <h5 class="mb-3">
        <i class="bi bi-person-circle me-2" />
        Profile
      </h5>
      <PasskeySection />
      <SessionsSection />
      <PushDevicesSection />
    </div>
  );
}
