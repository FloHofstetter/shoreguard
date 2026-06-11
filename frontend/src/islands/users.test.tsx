/** Render tests for the users island against a mocked fetch. */

import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import UsersPage from "./users";

const USERS = [
  {
    id: 1,
    email: "admin@test.com",
    role: "admin",
    created_at: "2026-01-01T00:00:00Z",
    pending_invite: false,
  },
  {
    id: 2,
    email: "viewer@test.com",
    role: "viewer",
    created_at: "2026-02-01T00:00:00Z",
    pending_invite: true,
  },
];

const SPS = [
  {
    id: 7,
    name: "ci-bot",
    role: "operator",
    key_prefix: "sg_abc",
    expires_at: null,
    last_used: null,
  },
];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body = String(url).includes("service-principals") ? SPS : USERS;
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  vi.stubGlobal("showToast", vi.fn());
  vi.stubGlobal(
    "showConfirm",
    vi.fn(async () => true),
  );
});

test("renders users and service principals from the API", async () => {
  render(<UsersPage />);
  await waitFor(() => {
    expect(screen.getByText("admin@test.com")).toBeTruthy();
  });
  expect(screen.getByText("viewer@test.com")).toBeTruthy();
  expect(screen.getByText("Invited")).toBeTruthy();
  expect(screen.getByText("ci-bot")).toBeTruthy();
  expect(screen.getByText("sg_abc...")).toBeTruthy();
});

test("filter narrows the user table", async () => {
  const { container } = render(<UsersPage />);
  await waitFor(() => {
    expect(container.querySelector("input[placeholder='Filter users...']")).toBeTruthy();
  });
  const input = container.querySelector<HTMLInputElement>("input[placeholder='Filter users...']")!;
  input.value = "viewer";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await waitFor(() => {
    expect(screen.queryByText("admin@test.com")).toBeNull();
  });
  expect(screen.getByText("viewer@test.com")).toBeTruthy();
});
