/** Topbar widgets: gateway switcher dot/dropdown and the auth area. */

import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { auth, logout } from "../lib/auth";
import { GW } from "../lib/constants";
import { health } from "../lib/health";

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

export function AuthArea() {
  const a = auth.value;
  if (!a.authenticated) return null;
  return (
    <span class="d-flex align-items-center">
      {a.email && <span class="text-muted small ms-2">{a.email}</span>}
      <span class="badge bg-secondary ms-2">{a.role}</span>
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
