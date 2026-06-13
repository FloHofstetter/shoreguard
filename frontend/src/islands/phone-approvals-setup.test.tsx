/** The phone-approvals wizard wires the right webhook and is idempotent. */

import { expect, test } from "vitest";

import { APPROVAL_EVENTS, buildWebhookBody, hasApprovalWebpush } from "./phone-approvals-setup";

test("webhook body targets the webpush channel and the approval events", () => {
  const body = buildWebhookBody();
  expect(body.channel_type).toBe("webpush");
  expect(body.url).toBe("webpush:all");
  expect(body.event_types).toContain("approval.pending");
  expect(body.event_types).toContain("budget.exceeded");
  expect(body.event_types).toEqual(APPROVAL_EVENTS);
});

test("detects an existing webpush approval webhook (idempotency)", () => {
  expect(
    hasApprovalWebpush([
      { channel_type: "slack", url: "https://hooks…", event_types: ["approval.pending"] },
      { channel_type: "webpush", url: "webpush:all", event_types: ["approval.pending", "x"] },
    ]),
  ).toBe(true);
});

test("does not match a webpush hook that lacks the approval event", () => {
  expect(
    hasApprovalWebpush([{ channel_type: "webpush", url: "webpush:all", event_types: ["sandbox.created"] }]),
  ).toBe(false);
});

test("no webhooks means setup must create one", () => {
  expect(hasApprovalWebpush([])).toBe(false);
});
