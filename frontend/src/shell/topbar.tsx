/** Topbar widgets: gateway switcher dot/dropdown and the auth area. */

import { useEffect, useRef, useState } from "preact/hooks";
import { renderSVG } from "uqr";

import { apiFetch } from "../lib/api";
import { auth, logout } from "../lib/auth";
import { GW } from "../lib/constants";
import { health } from "../lib/health";
import { Modal } from "../lib/Modal";
import { showToast } from "../lib/notify";
import { isLoopbackHostname, rewriteToCandidate } from "../lib/phone-url";
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

interface AccessUrls {
  bind_host: string;
  port: number;
  loopback_only: boolean;
  lan_urls: string[];
}

function PhoneQrContent() {
  const onLoopback = isLoopbackHostname(window.location.hostname);
  const [access, setAccess] = useState<AccessUrls | null>(null);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState(0);

  useEffect(() => {
    if (!onLoopback) return;
    apiFetch<AccessUrls>(`/api/system/access-urls`)
      .then(setAccess)
      .catch(() => setFailed(true));
  }, [onLoopback]);

  if (onLoopback && !access && !failed) {
    return (
      <div class="text-muted py-4">
        <span class="spinner-border spinner-border-sm me-1" />
        Checking how this server is reachable…
      </div>
    );
  }

  if (onLoopback && access?.loopback_only) {
    return (
      <>
        <div class="alert alert-warning text-start small mb-0">
          <i class="bi bi-exclamation-triangle me-1" />
          The server only listens on <code>{access.bind_host}</code> — your phone cannot reach
          it{access.lan_urls.length > 0 && <>, even though this machine is on the network</>}.
          Restart with <code>--host 0.0.0.0</code> to serve the LAN
          {access.lan_urls.length > 0 && (
            <>
              {" "}
              (then it will be reachable at <code>{access.lan_urls[0]}</code>)
            </>
          )}
          , or keep the loopback bind and put <code>tailscale serve</code> in front.
        </div>
        <PushToggle />
      </>
    );
  }

  // On loopback with LAN candidates, re-host the current page onto one of
  // them; otherwise the current location already works from the phone.
  const candidates = onLoopback && access ? access.lan_urls : [];
  const url =
    candidates.length > 0
      ? rewriteToCandidate(window.location.href, candidates[Math.min(selected, candidates.length - 1)])
      : window.location.href;
  const stillLoopback = isLoopbackHostname(new URL(url).hostname);

  return (
    <>
      <div
        class="bg-white d-inline-block p-2 rounded"
        style={{ width: "220px" }}
        // Self-generated SVG (uqr) from the current location or the
        // server-reported LAN address — no external content reaches
        // this sink.
        dangerouslySetInnerHTML={{ __html: renderSVG(url) }}
      />
      <div class="small text-muted font-monospace mt-2">{url}</div>
      {candidates.length > 1 && (
        <div class="d-flex justify-content-center gap-1 mt-2">
          {candidates.map((c, i) => (
            <button
              key={c}
              class={`btn btn-sm ${i === selected ? "btn-secondary" : "btn-outline-secondary"}`}
              onClick={() => setSelected(i)}
            >
              {new URL(c).hostname}
            </button>
          ))}
        </div>
      )}
      {stillLoopback ? (
        <div class="alert alert-warning text-start small mt-2 mb-0">
          <i class="bi bi-exclamation-triangle me-1" />
          This is a loopback address — it only works on this machine. Your phone needs a LAN
          address (<code>--host 0.0.0.0</code>) or a tailnet (<code>tailscale serve</code>).
        </div>
      ) : (
        url.startsWith("http:") && (
          <div class="small text-muted mt-2">
            The dashboard works over plain HTTP, but push notifications on the phone need
            HTTPS — e.g. <code>tailscale serve</code> in front of this server.
          </div>
        )
      )}
      <PushToggle />
    </>
  );
}

function PhoneAccessButton() {
  const [open, setOpen] = useState(false);
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
            <PhoneQrContent />
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
      {a.email && (
        <a href="/profile" class="text-muted small ms-2 text-decoration-none" title="Profile">
          {a.email}
        </a>
      )}
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
