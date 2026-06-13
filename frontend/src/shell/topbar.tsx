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
  const [mode, setMode] = useState<"page" | "signin">("page");
  const deviceLinkEnabled = auth.value.deviceLinkEnabled;

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
  const canSignin = deviceLinkEnabled && !stillLoopback;

  return (
    <>
      {canSignin && (
        <div class="btn-group btn-group-sm mb-3" role="group">
          <button
            class={`btn ${mode === "page" ? "btn-secondary" : "btn-outline-secondary"}`}
            onClick={() => setMode("page")}
          >
            <i class="bi bi-window me-1" />
            Open page
          </button>
          <button
            class={`btn ${mode === "signin" ? "btn-secondary" : "btn-outline-secondary"}`}
            onClick={() => setMode("signin")}
          >
            <i class="bi bi-box-arrow-in-right me-1" />
            Sign in on phone
          </button>
        </div>
      )}

      {mode === "signin" && canSignin ? (
        <DeviceLinkPanel origin={new URL(url).origin} />
      ) : (
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
      )}
    </>
  );
}

interface PendingRequest {
  id: number;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
}

function DeviceLinkPanel({ origin }: { origin: string }) {
  const [code, setCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState(0);
  const [pending, setPending] = useState<PendingRequest[]>([]);
  const [error, setError] = useState("");
  const [now, setNow] = useState(0);

  const mint = async () => {
    setError("");
    setCode(null);
    setPending([]);
    try {
      const r = await apiFetch<{ code: string; expires_at: string }>(`/api/auth/device-link`, {
        method: "POST",
      });
      setCode(r.code);
      setExpiresAt(new Date(r.expires_at).getTime());
      setNow(Date.now());
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Mint a code as soon as the operator opens this panel.
  useEffect(() => {
    void mint();
  }, []);

  // 1 Hz tick drives the expiry countdown.
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  // Poll for devices that have claimed the code and await approval.
  useEffect(() => {
    if (!code) return;
    let active = true;
    let timer = 0;
    const poll = async () => {
      try {
        const r = await apiFetch<{ pending: PendingRequest[] }>(`/api/auth/device-link/pending`);
        if (active) setPending(r?.pending ?? []);
      } catch {
        /* transient — keep polling */
      }
      if (active) timer = window.setTimeout(poll, 2000);
    };
    void poll();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [code]);

  const decide = async (id: number, approve: boolean) => {
    try {
      await apiFetch(`/api/auth/device-link/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, approve }),
      });
      setPending((p) => p.filter((x) => x.id !== id));
      showToast(approve ? "Device approved — it is signing in." : "Request denied.",
        approve ? "success" : "info");
    } catch (e) {
      showToast((e as Error).message, "danger");
    }
  };

  if (error) {
    return (
      <div class="alert alert-danger small mb-0">
        <i class="bi bi-exclamation-octagon me-1" />
        {error}
      </div>
    );
  }
  if (!code) {
    return (
      <div class="text-muted py-4">
        <span class="spinner-border spinner-border-sm me-1" />
        Generating a one-time sign-in code…
      </div>
    );
  }

  const remaining = Math.max(0, Math.round((expiresAt - now) / 1000));
  const url = `${origin}/login/device#${code}`;

  if (remaining <= 0 && pending.length === 0) {
    return (
      <div class="text-center">
        <p class="text-muted small">This sign-in code expired.</p>
        <button class="btn btn-sm btn-outline-primary" onClick={() => void mint()}>
          <i class="bi bi-arrow-clockwise me-1" />
          Generate a new code
        </button>
      </div>
    );
  }

  return (
    <div class="text-center">
      {pending.length > 0 ? (
        <div class="alert alert-warning text-start mb-0">
          <div class="fw-medium mb-2">
            <i class="bi bi-phone-vibrate me-1" />
            A device wants to sign in as you
          </div>
          {pending.map((p) => (
            <div key={p.id} class="small mb-2">
              <div class="text-muted font-monospace">{p.ip ?? "unknown IP"}</div>
              {p.user_agent && <div class="text-muted text-truncate">{p.user_agent}</div>}
              <div class="d-flex gap-2 mt-2">
                <button class="btn btn-sm btn-success" onClick={() => void decide(p.id, true)}>
                  <i class="bi bi-check-lg me-1" />
                  Approve
                </button>
                <button class="btn btn-sm btn-outline-danger" onClick={() => void decide(p.id, false)}>
                  <i class="bi bi-x-lg me-1" />
                  Deny
                </button>
              </div>
            </div>
          ))}
          <div class="small text-muted">
            Only approve if you are holding the phone that just scanned the code.
          </div>
        </div>
      ) : (
        <>
          <div
            class="bg-white d-inline-block p-2 rounded"
            style={{ width: "220px" }}
            // Self-generated SVG (uqr) of a one-time, server-minted
            // device-link URL — no external content reaches this sink.
            dangerouslySetInnerHTML={{ __html: renderSVG(url) }}
          />
          <div class="small text-muted mt-2">
            Scan to sign in — expires in {remaining}s. You will approve the request here.
          </div>
        </>
      )}
      {url.startsWith("http:") && (
        <div class="small text-muted mt-2">
          Sent over plain HTTP on the LAN. For a credential like this, prefer HTTPS
          (<code>tailscale serve</code>).
        </div>
      )}
    </div>
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
        <a
          href="/profile"
          class="text-muted text-decoration-none ms-2"
          title={`Profile (${a.email})`}
        >
          <span class="small d-none d-md-inline">{a.email}</span>
          <i class="bi bi-person-circle fs-5 d-md-none" />
        </a>
      )}
      <span class="badge text-bg-secondary ms-2 d-none d-sm-inline-block">{a.role}</span>
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
