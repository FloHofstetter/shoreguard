/** Topbar widgets: gateway switcher dot/dropdown and the auth area. */

import { useEffect, useRef, useState } from "preact/hooks";
import { renderSVG } from "uqr";

import { apiFetch } from "../lib/api";
import { auth, logout } from "../lib/auth";
import { GW } from "../lib/constants";
import { health } from "../lib/health";
import { Modal } from "../lib/Modal";
import { showToast } from "../lib/notify";
import { currentSubscription, disablePush, enablePush, pushSupported, sendTestPush } from "../lib/push";

interface GatewayItem {
  name: string;
  status?: string;
  labels?: Record<string, string>;
}

function statusDotClass(status: string | undefined): string {
  if (status === "connected") return "text-success";
  if (status === "unreachable" || status === "offline") return "text-danger";
  return "text-muted";
}

export function GatewaySwitcher() {
  const [open, setOpen] = useState(false);
  const [gateways, setGateways] = useState<GatewayItem[] | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onOutside = (e: MouseEvent) => {
      if (open && rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("click", onOutside);
    return () => document.removeEventListener("click", onOutside);
  }, [open]);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && gateways === null) {
      try {
        const resp = await apiFetch<{ items?: GatewayItem[] }>(`/api/gateway/list`);
        setGateways(resp?.items ?? []);
      } catch {
        setGateways([]);
      }
    }
  };

  const h = health.value;
  const dotClass = h.connected
    ? "text-success"
    : h.status !== "unknown"
      ? "text-danger"
      : "text-muted";

  return (
    <div ref={rootRef} class="topbar-gateway-switcher position-relative ms-2">
      <button
        type="button"
        class="btn btn-sm btn-outline-secondary d-flex align-items-center gap-1"
        title={GW ? `Active gateway: ${GW}` : "Switch gateway"}
        onClick={() => void toggle()}
      >
        <i class="bi bi-hdd-network" />
        {GW && <i class={`bi bi-circle-fill small ${dotClass}`} />}
        <span class="fw-medium">{GW || "Switch gateway"}</span>
        <i class="bi bi-chevron-down small opacity-75" />
      </button>
      <div class={`dropdown-menu dropdown-menu-end mt-1 sg-mw-300 ${open ? "show" : ""}`}>
        {open && gateways === null && (
          <div class="text-muted small px-3 py-2">
            <span class="spinner-border spinner-border-sm me-1" />
            Loading…
          </div>
        )}
        {(gateways ?? []).map((gw) => (
          <a
            key={gw.name}
            href={`/gateways/${gw.name}`}
            class={`dropdown-item d-flex justify-content-between align-items-center gap-2 ${
              gw.name === GW ? "active" : ""
            }`}
          >
            <span class="d-flex align-items-center gap-2">
              <i class={`bi bi-circle-fill small ${statusDotClass(gw.status)}`} />
              <span class="fw-medium">{gw.name}</span>
            </span>
            {gw.labels && Object.keys(gw.labels).length > 0 && (
              <span class="text-muted small text-truncate">
                {Object.entries(gw.labels)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(" ")}
              </span>
            )}
          </a>
        ))}
        {gateways !== null && gateways.length === 0 && (
          <div class="text-muted small px-3 py-2">No gateways registered.</div>
        )}
        {gateways !== null && gateways.length > 0 && <div class="dropdown-divider" />}
        <a href="/gateways" class="dropdown-item small text-muted">
          <i class="bi bi-list me-1" />
          Manage gateways
        </a>
      </div>
    </div>
  );
}

function PushToggle() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    currentSubscription()
      .then((sub) => setEnabled(sub !== null))
      .catch(() => setEnabled(false));
  }, []);

  if (!pushSupported()) {
    return (
      <div class="small text-muted mt-3">
        <i class="bi bi-bell-slash me-1" />
        Push notifications need HTTPS (or localhost) and a modern browser.
      </div>
    );
  }

  const toggle = async () => {
    setBusy(true);
    try {
      if (enabled) {
        await disablePush();
        setEnabled(false);
        showToast("Push notifications disabled on this device.", "info");
      } else {
        await enablePush();
        setEnabled(true);
        showToast("Push notifications enabled on this device.", "success");
      }
    } catch (e) {
      showToast((e as Error).message, "danger");
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    try {
      const r = await sendTestPush();
      showToast(`Test notification sent to ${r.sent} device(s).`, "success");
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  return (
    <div class="mt-3 border-top pt-3">
      <div class="d-flex justify-content-center gap-2">
        <button class="btn btn-sm btn-outline-primary" disabled={busy} onClick={() => void toggle()}>
          <i class={`bi ${enabled ? "bi-bell-slash" : "bi-bell"} me-1`} />
          {enabled ? "Disable push on this device" : "Enable push on this device"}
        </button>
        {enabled && (
          <button class="btn btn-sm btn-outline-secondary" onClick={() => void test()}>
            Test
          </button>
        )}
      </div>
      <div class="small text-muted mt-2">
        Pair with a <code>webpush</code> webhook to choose which events reach this device.
      </div>
    </div>
  );
}

function PhoneAccessButton() {
  const [open, setOpen] = useState(false);
  const url = window.location.href;
  return (
    <>
      <button
        class="btn btn-sm btn-outline-secondary ms-2"
        title="Open on phone"
        aria-label="Open on phone"
        onClick={() => setOpen(true)}
      >
        <i class="bi bi-qr-code" />
      </button>
      {open && (
        <Modal
          onClose={() => setOpen(false)}
          title={
            <span>
              <i class="bi bi-phone me-2" />
              Open on phone
            </span>
          }
        >
          <div class="text-center">
            <div
              class="bg-white d-inline-block p-2 rounded"
              style={{ width: "220px" }}
              // Self-generated SVG (uqr) from the current location — no
              // external content reaches this sink.
              dangerouslySetInnerHTML={{ __html: renderSVG(url) }}
            />
            <div class="small text-muted font-monospace mt-2">{url}</div>
            <div class="small text-muted mt-2">
              Your phone must reach this address — same network, or better: a
              tailnet (<code>tailscale serve</code> in front of a loopback bind).
            </div>
            <PushToggle />
          </div>
        </Modal>
      )}
    </>
  );
}

export function AuthArea() {
  const a = auth.value;
  if (!a.authenticated) return null;
  return (
    <span class="d-flex align-items-center">
      {a.email && <span class="text-muted small ms-2">{a.email}</span>}
      <span class="badge bg-secondary ms-2">{a.role}</span>
      <PhoneAccessButton />
      <button
        class="btn btn-sm btn-outline-secondary ms-2"
        title="Log out"
        aria-label="Log out"
        onClick={() => void logout()}
      >
        <i class="bi bi-box-arrow-right" />
      </button>
    </span>
  );
}
