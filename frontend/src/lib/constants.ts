/** UI-only constants: presentation mappings and tuning knobs. */

/** Gateway name injected by base.html for gateway-scoped pages. */
export const GW: string = document.documentElement.dataset.gateway ?? "";

/** Gateway-scoped API prefix (falls back to /api on global pages). */
export const API: string = GW ? `/api/gateways/${GW}` : "/api";

/** Prefix a page path with the current gateway scope. */
export function gwUrl(path: string): string {
  return GW ? `/gateways/${GW}${path}` : path;
}

export function navigateTo(path: string): void {
  window.location.href = path;
}

export const BADGES: Record<string, Record<string, string>> = {
  phase: {
    ready: "text-bg-success",
    provisioning: "text-bg-warning",
    error: "text-bg-danger",
    deleting: "text-bg-secondary",
    unknown: "text-bg-secondary",
  },
  approval: {
    pending: "text-bg-warning",
    approved: "text-bg-success",
    rejected: "text-bg-danger",
  },
  gateway: {
    connected: "text-bg-success",
    running: "text-bg-info",
    unreachable: "text-bg-warning",
    stopped: "text-bg-secondary",
    offline: "text-bg-danger",
  },
  role: {
    admin: "text-bg-danger",
    operator: "text-bg-warning",
    viewer: "text-bg-secondary",
  },
  severity: {
    ok: "text-bg-success",
    info: "text-bg-info",
    low: "text-bg-info",
    medium: "text-bg-warning",
    med: "text-bg-warning",
    warn: "text-bg-warning",
    warning: "text-bg-warning",
    high: "text-bg-danger",
    error: "text-bg-danger",
    critical: "text-bg-danger",
    crit: "text-bg-danger",
    fatal: "text-bg-dark",
  },
};

export function badgeClass(group: keyof typeof BADGES, value: string | undefined): string {
  return BADGES[group]?.[value?.toLowerCase() ?? ""] ?? "text-bg-secondary";
}

export const GATEWAY_TYPE_ICONS: Record<string, { icon: string; label: string }> = {
  local: { icon: "pc-display", label: "Local" },
  remote: { icon: "globe", label: "Remote" },
  cloud: { icon: "cloud", label: "Cloud" },
};

export const CONFIG = {
  healthCheckInterval: 10000,
  healthCheckFallback: 5000,
  toastDelay: 4000,
  approvalToastDelay: 10000,
  actionRefreshDelay: 2000,
  wsMaxBackoff: 30000,
  wsHeartbeatTimeout: 45000,
  wsMaxRetries: 20,
  logLinesDefault: 200,
  wizardStepDelay: 200,
} as const;
