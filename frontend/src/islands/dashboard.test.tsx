/** buildActivity joins fleet-wide sandboxes with usage + pending approvals. */

import { expect, test } from "vitest";

import { buildActivity } from "./dashboard";

const FLAT = [
  { gateway: "local-demo", name: "claude-dev", phase: "ready" },
  { gateway: "local-demo", name: "nightly", phase: "stopped" },
  { gateway: "edge-prod", name: "claude-dev", phase: "ready" }, // same name, other gateway
];

const USAGE = [
  { gateway: "local-demo", sandbox: "claude-dev", requests: 1342 },
  { gateway: "local-demo", sandbox: "nightly", requests: 7 },
  { gateway: "edge-prod", sandbox: "claude-dev", requests: 50 },
];

test("joins usage by gateway+name, not name alone", () => {
  const rows = buildActivity(FLAT, USAGE, [3, 0, 1]);
  const find = (gw: string, name: string) =>
    rows.find((r) => r.gateway === gw && r.name === name);
  expect(find("local-demo", "claude-dev")).toEqual({
    gateway: "local-demo",
    name: "claude-dev",
    phase: "ready",
    requests: 1342,
    pending: 3,
  });
  // same sandbox name on a different gateway keeps its own (50) requests
  expect(find("edge-prod", "claude-dev")).toEqual({
    gateway: "edge-prod",
    name: "claude-dev",
    phase: "ready",
    requests: 50,
    pending: 1,
  });
});

test("sorts the busiest sandboxes first", () => {
  const rows = buildActivity(FLAT, USAGE, [3, 0, 1]);
  expect(rows.map((r) => r.requests)).toEqual([1342, 50, 7]);
});

test("defaults missing usage and pending to 0", () => {
  const rows = buildActivity([{ gateway: "g", name: "x", phase: "ready" }], [], []);
  expect(rows[0]).toEqual({ gateway: "g", name: "x", phase: "ready", requests: 0, pending: 0 });
});

test("pending counts sum to the approvals total", () => {
  const rows = buildActivity(FLAT, USAGE, [3, 3, 3]);
  expect(rows.reduce((n, r) => n + r.pending, 0)).toBe(9);
});
