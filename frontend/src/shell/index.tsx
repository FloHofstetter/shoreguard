/**
 * App shell: wires the server-rendered chrome (topbar, sidebar) to the
 * Preact widgets and global behaviours that used to live in Alpine —
 * gateway switcher, auth area, theme toggle, health indicator, command
 * palette, keyboard shortcuts, and role-based element visibility.
 *
 * Mount points are plain elements in base.html identified by id; pages
 * without them (standalone auth pages) skip silently.
 */

import { render } from "preact";

import { auth, ensureAuth, hasRole } from "../lib/auth";
import { GW, gwUrl, navigateTo } from "../lib/constants";
import { health, startHealthPolling } from "../lib/health";
import { CommandPalette } from "./palette";
import { AuthArea, GatewaySwitcher } from "./topbar";

function ThemeToggle() {
  const current = document.documentElement.getAttribute("data-bs-theme") || "dark";
  const toggle = () => {
    const next =
      document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-bs-theme", next);
    try {
      localStorage.setItem("sg-theme", next);
    } catch {
      // ignore
    }
    // re-render label
    mountInto("sg-theme-toggle", <ThemeToggle />);
  };
  return (
    <button class="sidebar-link sg-btn-bare" onClick={toggle}>
      <i class={`bi ${current === "dark" ? "bi-sun" : "bi-moon"}`} />
      <span>{current === "dark" ? "Light Mode" : "Dark Mode"}</span>
    </button>
  );
}

function SidebarHealth() {
  if (!GW) return null;
  const h = health.value;
  const cls = h.connected ? "text-success" : h.status !== "unknown" ? "text-danger" : "text-muted";
  return (
    <div class={`sidebar-health ${cls}`}>
      <i class="bi bi-circle-fill" />
      <span>
        {h.connected
          ? h.version || "Connected"
          : h.status === "degraded"
            ? "Degraded"
            : "Disconnected"}
      </span>
    </div>
  );
}

function mountInto(id: string, node: preact.JSX.Element): void {
  const el = document.getElementById(id);
  if (el) render(node, el);
}

function wireSidebar(): void {
  const sidebar = document.querySelector<HTMLElement>(".sidebar");
  const backdrop = document.querySelector<HTMLElement>(".sidebar-backdrop");
  const toggleBtn = document.querySelector<HTMLElement>(".sidebar-toggle");
  if (!sidebar) return;
  const setOpen = (open: boolean) => {
    sidebar.classList.toggle("open", open);
    backdrop?.classList.toggle("show", open);
    if (backdrop) backdrop.style.display = open ? "block" : "none";
  };
  toggleBtn?.addEventListener("click", () => setOpen(!sidebar.classList.contains("open")));
  backdrop?.addEventListener("click", () => setOpen(false));
}

function applyRoleVisibility(): void {
  document.querySelectorAll<HTMLElement>("[data-sg-min-role]").forEach((el) => {
    const minRole = el.getAttribute("data-sg-min-role") || "admin";
    if (!hasRole(minRole)) el.style.display = "none";
  });
}

function wireKeyboardShortcuts(): void {
  let pendingKey: string | null = null;
  document.addEventListener("keydown", (e) => {
    const target = e.target as HTMLElement;
    if (target.matches("input, textarea, select, [contenteditable]")) return;

    if (pendingKey === "g") {
      pendingKey = null;
      const routes: Record<string, string> = {
        d: "/",
        g: "/gateways",
        a: "/audit",
        u: "/users",
        p: "/policies",
        r: "/groups",
      };
      if (GW) routes.s = gwUrl("/sandboxes");
      if (routes[e.key]) {
        e.preventDefault();
        navigateTo(routes[e.key]);
      }
      return;
    }

    if (e.key === "g" && !e.metaKey && !e.ctrlKey && !e.altKey) {
      pendingKey = "g";
      setTimeout(() => {
        pendingKey = null;
      }, 1000);
      return;
    }

    if (e.key === "?" && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      const modal = document.getElementById("shortcutsModal");
      const bs = (window as { bootstrap?: { Modal: new (el: Element) => { show(): void } } })
        .bootstrap;
      if (modal && bs) new bs.Modal(modal).show();
    }
  });
}

/** Initialise the shell on pages that extend base.html. */
export function initShell(): void {
  if (!document.getElementById("sg-shell")) return;

  void ensureAuth().then(() => {
    applyRoleVisibility();
    mountInto("sg-auth-area", <AuthArea />);
    // Keep the auth area reactive (it reads the auth signal).
    auth.subscribe(() => mountInto("sg-auth-area", <AuthArea />));
  });

  startHealthPolling();
  health.subscribe(() => {
    mountInto("sg-sidebar-health", <SidebarHealth />);
    mountInto("sg-gateway-switcher", <GatewaySwitcher />);
  });

  mountInto("sg-gateway-switcher", <GatewaySwitcher />);
  mountInto("sg-theme-toggle", <ThemeToggle />);
  mountInto("sg-sidebar-health", <SidebarHealth />);

  const paletteRoot = document.createElement("div");
  document.body.appendChild(paletteRoot);
  render(<CommandPalette />, paletteRoot);

  wireSidebar();
  wireKeyboardShortcuts();
}
