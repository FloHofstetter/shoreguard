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

export default function ProfilePage() {
  return (
    <div>
      <h5 class="mb-3">
        <i class="bi bi-person-circle me-2" />
        Profile
      </h5>
      <PasskeySection />
      <PushDevicesSection />
    </div>
  );
}
