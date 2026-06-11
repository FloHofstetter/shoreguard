/** Shared auth state for islands and the app shell. */

import { signal } from "@preact/signals";

export interface AuthState {
  authenticated: boolean;
  role: string;
  email: string | null;
  registrationEnabled: boolean;
  localMode: boolean;
  loaded: boolean;
}

export const auth = signal<AuthState>({
  authenticated: false,
  role: "viewer",
  email: null,
  registrationEnabled: false,
  localMode: false,
  loaded: false,
});

const ROLE_RANKS: Record<string, number> = { admin: 2, operator: 1, viewer: 0 };

export function hasRole(minimum: string): boolean {
  return (ROLE_RANKS[auth.value.role] ?? 0) >= (ROLE_RANKS[minimum] ?? 99);
}

let authPromise: Promise<void> | null = null;

/** Fetch /api/auth/check once per page; redirects to /setup when needed. */
export function ensureAuth(): Promise<void> {
  if (!authPromise) {
    authPromise = (async () => {
      try {
        const resp = await fetch("/api/auth/check");
        const d = await resp.json();
        if (d.needs_setup) {
          if (!window.location.pathname.startsWith("/setup")) {
            window.location.href = `/setup?next=${encodeURIComponent(window.location.pathname)}`;
          }
          return;
        }
        auth.value = {
          authenticated: Boolean(d.authenticated),
          role: d.role || "viewer",
          email: d.email || null,
          registrationEnabled: Boolean(d.registration_enabled),
          localMode: Boolean(d.local_mode),
          loaded: true,
        };
      } catch {
        auth.value = { ...auth.value, loaded: true };
      }
    })();
  }
  return authPromise;
}

export async function logout(): Promise<void> {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } finally {
    window.location.href = "/login";
  }
}
