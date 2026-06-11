/**
 * Island bootstrap: mounts Preact components into server-rendered pages.
 *
 * A page opts in with `<div data-island="<name>"></div>`; the matching
 * component is loaded on demand (code-split chunk) and rendered into
 * the element. Pages without islands pay only for this tiny scanner.
 */

import { h, render } from "preact";

// deno-lint-ignore-file
// Props are validated by each island; the registry is intentionally loose.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type IslandComponent = (props: any) => unknown;
type IslandLoader = () => Promise<{ default: IslandComponent }>;

const ISLANDS: Record<string, IslandLoader> = {
  "users-page": () => import("./islands/users"),
  "groups-page": () => import("./islands/groups"),
  "webhooks-page": () => import("./islands/webhooks"),
  "audit-page": () => import("./islands/audit"),
  "dashboard-page": () => import("./islands/dashboard"),
  "provider-profiles-page": () => import("./islands/provider-profiles"),
  "services-page": () => import("./islands/services"),
  "auth-login": () => import("./islands/auth-pages").then((m) => ({ default: m.LoginPage })),
  "auth-register": () => import("./islands/auth-pages").then((m) => ({ default: m.RegisterPage })),
  "auth-setup": () => import("./islands/auth-pages").then((m) => ({ default: m.SetupPage })),
  "auth-invite": () => import("./islands/auth-pages").then((m) => ({ default: m.InvitePage })),
  "user-new-form": () => import("./islands/user-forms").then((m) => ({ default: m.UserNewForm })),
  "sp-new-form": () => import("./islands/user-forms").then((m) => ({ default: m.SpNewForm })),
  "sandboxes-page": () => import("./islands/sandboxes"),
  "sandbox-detail": () => import("./islands/sandbox-detail"),
  "sandbox-nav-delete": () => import("./islands/sandbox-nav-delete"),
  "sandbox-terminal": () => import("./islands/sandbox-terminal"),
  "sandbox-logs": () => import("./islands/sandbox-logs"),
  "sandbox-sbom": () => import("./islands/sandbox-sbom"),
  "sandbox-bypass": () => import("./islands/sandbox-bypass"),
  "sandbox-prover": () => import("./islands/sandbox-prover"),
  "sandbox-hooks": () => import("./islands/sandbox-hooks"),
  "sandbox-forward": () => import("./islands/sandbox-forward"),
  "gateways-page": () => import("./islands/gateways"),
  "gateway-detail": () => import("./islands/gateway-detail"),
  "gateway-register": () => import("./islands/gateway-register"),
  "providers-page": () => import("./islands/providers"),
  "provider-form": () => import("./islands/providers").then((m) => ({ default: m.ProviderForm })),
  "wizard-page": () => import("./islands/wizard"),
};

async function mountIslands(): Promise<void> {
  for (const el of document.querySelectorAll<HTMLElement>("[data-island]")) {
    const name = el.dataset.island ?? "";
    const loader = ISLANDS[name];
    if (!loader) {
      console.error(`Unknown island: ${name}`);
      continue;
    }
    const props = el.dataset.props ? JSON.parse(el.dataset.props) : {};
    const { default: Component } = await loader();
    el.replaceChildren();
    render(h(Component as never, props), el);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => void mountIslands());
} else {
  void mountIslands();
}
