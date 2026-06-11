/**
 * Typed fetch wrapper for the ShoreGuard REST API.
 *
 * Mirrors the legacy `apiFetch` contract: redirects to /login on 401,
 * surfaces `detail` from problem responses, returns parsed JSON (or
 * null for empty bodies). Call sites use template literals so the
 * surface-coverage gate (scripts/check_coverage.py) can discover them.
 */

export async function apiFetch<T = unknown>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const resp = await fetch(url, options);
  if (resp.status === 401) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    throw new Error("Authentication required");
  }
  if (!resp.ok) {
    let detail = "";
    try {
      const body = await resp.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await resp.text();
    }
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  const text = await resp.text();
  return (text ? JSON.parse(text) : null) as T;
}

// ── Resource types (hand-maintained until OpenAPI generation lands) ──

export interface User {
  id: number;
  email: string;
  role: string;
  created_at: string | null;
  oidc_provider?: string | null;
  pending_invite?: boolean;
}

export interface ServicePrincipal {
  id: number;
  name: string;
  role: string;
  key_prefix?: string | null;
  expires_at?: string | null;
  last_used?: string | null;
}

export interface GatewayRole {
  gateway_name: string;
  role: string;
}

export interface GatewaySummary {
  name: string;
  endpoint?: string;
  status?: string;
  connected?: boolean;
}
