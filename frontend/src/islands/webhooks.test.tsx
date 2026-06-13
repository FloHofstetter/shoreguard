/** Pausing a webhook is confirm-guarded: cancelling fires no request. */

import { cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import WebhooksPage from "./webhooks";

const WEBHOOK = {
  id: "wh_1",
  url: "https://example.com/hook",
  channel_type: "generic",
  event_types: ["sandbox.created"],
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
};

let calls: { url: string; method: string }[] = [];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: (init?.method ?? "GET").toUpperCase() });
      return new Response(JSON.stringify({ items: [WEBHOOK] }), { status: 200 });
    }),
  );
  vi.stubGlobal("showToast", vi.fn());
});

test("cancelling the Pause confirm makes no PUT request", async () => {
  vi.stubGlobal(
    "showConfirm",
    vi.fn(async () => false),
  );
  const { container } = render(<WebhooksPage />);
  await waitFor(() => {
    expect(container.querySelector("button[title='Pause']")).toBeTruthy();
  });
  fireEvent.click(container.querySelector<HTMLButtonElement>("button[title='Pause']")!);
  await waitFor(() => {
    expect((window as { showConfirm?: ReturnType<typeof vi.fn> }).showConfirm).toHaveBeenCalled();
  });
  expect(calls.some((c) => c.method === "PUT")).toBe(false);
});

test("confirming the Pause sends the PUT", async () => {
  vi.stubGlobal(
    "showConfirm",
    vi.fn(async () => true),
  );
  const { container } = render(<WebhooksPage />);
  await waitFor(() => {
    expect(container.querySelector("button[title='Pause']")).toBeTruthy();
  });
  fireEvent.click(container.querySelector<HTMLButtonElement>("button[title='Pause']")!);
  await waitFor(() => {
    expect(calls.some((c) => c.url.includes("/api/webhooks/wh_1") && c.method === "PUT")).toBe(true);
  });
});
