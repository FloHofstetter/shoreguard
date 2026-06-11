/**
 * Island bootstrap: mounts Preact components into server-rendered pages.
 *
 * A page opts in with `<div data-island="<name>"></div>`; the matching
 * component is loaded on demand (code-split chunk) and rendered into
 * the element. Pages without islands pay only for this tiny scanner.
 */

import { h, render } from "preact";

type IslandLoader = () => Promise<{ default: (props: Record<string, unknown>) => unknown }>;

const ISLANDS: Record<string, IslandLoader> = {
  "users-page": () => import("./islands/users"),
  "groups-page": () => import("./islands/groups"),
  "webhooks-page": () => import("./islands/webhooks"),
  "audit-page": () => import("./islands/audit"),
  "dashboard-page": () => import("./islands/dashboard"),
  "provider-profiles-page": () => import("./islands/provider-profiles"),
  "services-page": () => import("./islands/services"),
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
