/** Command palette (Ctrl+K / Cmd+K): nav, gateways, users, policies. */

import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { navigateTo } from "../lib/constants";

interface Item {
  name: string;
  url: string;
  icon: string;
  hint?: string;
}

type Row = { type: "group"; label: string } | ({ type: "item" } & Item);

const NAV_ITEMS: Item[] = [
  { name: "Dashboard", url: "/", icon: "bi-speedometer2" },
  { name: "Gateways", url: "/gateways", icon: "bi-hdd-network" },
  { name: "Policy Presets", url: "/policies", icon: "bi-shield-lock" },
  { name: "Audit Log", url: "/audit", icon: "bi-journal-text" },
  { name: "Groups", url: "/groups", icon: "bi-collection" },
  { name: "Users", url: "/users", icon: "bi-people" },
  { name: "Webhooks", url: "/webhooks", icon: "bi-broadcast" },
];

interface Cache {
  nav: Item[];
  gateways: Item[];
  policies: Item[];
  users: Item[];
}

async function buildCache(): Promise<Cache> {
  const cache: Cache = { nav: NAV_ITEMS, gateways: [], policies: [], users: [] };
  try {
    const gwData = await apiFetch<{ items?: { name: string; status?: string }[] }>(
      `/api/gateway/list`,
    );
    cache.gateways = (gwData?.items ?? []).map((g) => ({
      name: g.name,
      url: `/gateways/${g.name}`,
      icon: "bi-hdd-network",
      hint: g.status,
    }));
  } catch {
    // ignore
  }
  try {
    const presets = await apiFetch<{ name: string; description?: string }[]>(
      `/api/policies/presets`,
    );
    cache.policies = (presets ?? []).map((p) => ({
      name: p.name,
      url: `/policies/${p.name}`,
      icon: "bi-shield-lock",
      hint: p.description,
    }));
  } catch {
    // ignore
  }
  try {
    const data = await apiFetch<{ email: string; role: string }[]>(`/api/auth/users`);
    cache.users = (data ?? []).map((u) => ({
      name: u.email,
      url: "/users",
      icon: "bi-person",
      hint: u.role,
    }));
  } catch {
    // non-admin or unavailable
  }
  return cache;
}

const GROUP_LABELS: Record<keyof Cache, string> = {
  nav: "Navigation",
  gateways: "Gateways",
  policies: "Policies",
  users: "Users",
};

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const cacheRef = useRef<Cache | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [, forceUpdate] = useState(0);

  useEffect(() => {
    const onKeydown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
        setQuery("");
        setSelected(0);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKeydown);
    return () => document.removeEventListener("keydown", onKeydown);
  }, []);

  useEffect(() => {
    if (open) {
      if (!cacheRef.current) {
        void buildCache().then((cache) => {
          cacheRef.current = cache;
          forceUpdate((n) => n + 1);
        });
      }
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  if (!open) return null;

  const cache = cacheRef.current;
  const q = query.toLowerCase().trim();
  const rows: Row[] = [];
  if (cache) {
    for (const group of Object.keys(cache) as (keyof Cache)[]) {
      const matches = q
        ? cache[group].filter((i) => i.name.toLowerCase().includes(q))
        : cache[group];
      if (matches.length > 0) {
        rows.push({ type: "group", label: GROUP_LABELS[group] });
        for (const m of matches) rows.push({ type: "item", ...m });
      }
    }
  }

  let sel = selected;
  if (rows[sel]?.type !== "item") {
    sel = rows.findIndex((r) => r.type === "item");
  }

  const navigate = (row: Row) => {
    if (row.type !== "item") return;
    setOpen(false);
    navigateTo(row.url);
  };

  const moveSelection = (dir: 1 | -1) => {
    if (rows.length === 0) return;
    let idx = sel;
    do {
      idx = (idx + dir + rows.length) % rows.length;
    } while (rows[idx]?.type !== "item");
    setSelected(idx);
    requestAnimationFrame(() => {
      document.querySelector(".sg-palette-item.active")?.scrollIntoView({ block: "nearest" });
    });
  };

  const onKeydown = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveSelection(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveSelection(-1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (rows[sel]) navigate(rows[sel]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  return (
    <div
      class="sg-palette-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      <div class="sg-palette sg-fade-in">
        <input
          ref={inputRef}
          class="sg-palette-input"
          type="text"
          placeholder="Search pages, gateways, users..."
          autocomplete="off"
          value={query}
          onInput={(e) => {
            setQuery((e.target as HTMLInputElement).value);
            setSelected(0);
          }}
          onKeyDown={onKeydown}
        />
        <div class="sg-palette-results">
          {rows.length === 0 && query && (
            <div class="sg-palette-empty">
              No results for "<span>{query}</span>"
            </div>
          )}
          {rows.map((r, idx) =>
            r.type === "group" ? (
              <div key={`g-${r.label}`} class="sg-palette-group">
                {r.label}
              </div>
            ) : (
              <div
                key={`i-${idx}`}
                class={`sg-palette-item ${idx === sel ? "active" : ""}`}
                onClick={() => navigate(r)}
                onMouseEnter={() => setSelected(idx)}
              >
                <i class={`bi ${r.icon}`} />
                <span>{r.name}</span>
                {r.hint && <span class="sg-palette-hint">{r.hint}</span>}
              </div>
            ),
          )}
        </div>
        <div class="sg-palette-footer">
          <span>
            <kbd>↑↓</kbd> navigate
          </span>
          <span>
            <kbd>Enter</kbd> open
          </span>
          <span>
            <kbd>Esc</kbd> close
          </span>
        </div>
      </div>
    </div>
  );
}
