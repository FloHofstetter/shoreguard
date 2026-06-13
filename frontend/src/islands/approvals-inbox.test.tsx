/** flattenInbox flattens + prioritises pending approvals across the fleet. */

import { expect, test } from "vitest";

import { flattenInbox } from "./approvals-inbox";

const GROUPS = [
  {
    gateway: "local-demo",
    sandbox: "claude-dev",
    chunks: [
      { id: "a", rule_name: "pypi", confidence: 0.9, hit_count: 2 },
      { id: "b", rule_name: "dns", confidence: 0.99, hit_count: 1, security_notes: "exfil risk" },
    ],
  },
  {
    gateway: "edge-prod",
    sandbox: "nightly",
    chunks: [{ id: "c", rule_name: "apt", confidence: 0.5, hit_count: 9 }],
  },
];

test("flattens every chunk and stamps gateway + sandbox", () => {
  const rows = flattenInbox(GROUPS);
  expect(rows).toHaveLength(3);
  const b = rows.find((r) => r.id === "b")!;
  expect(b.gateway).toBe("local-demo");
  expect(b.sandbox).toBe("claude-dev");
});

test("security-flagged chunks sort first", () => {
  const rows = flattenInbox(GROUPS);
  expect(rows[0].id).toBe("b"); // the only security_notes chunk
});

test("after security, sorts by hit count then confidence", () => {
  const rows = flattenInbox([
    {
      gateway: "g",
      sandbox: "s",
      chunks: [
        { id: "low", confidence: 0.4, hit_count: 1 },
        { id: "high", confidence: 0.4, hit_count: 12 },
      ],
    },
  ]);
  expect(rows.map((r) => r.id)).toEqual(["high", "low"]);
});

test("empty fleet yields an empty inbox", () => {
  expect(flattenInbox([])).toEqual([]);
});
