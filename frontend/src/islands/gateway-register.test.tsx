/** Submitting an empty form shows an inline banner instead of relying on the native tooltip. */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, expect, test } from "vitest";

import GatewayRegisterPage from "./gateway-register";

afterEach(cleanup);

test("the form opts out of native validation (noValidate)", () => {
  const { container } = render(<GatewayRegisterPage />);
  const form = container.querySelector("form")!;
  expect(form.hasAttribute("novalidate")).toBe(true);
});

test("submitting empty shows a styled inline validation banner", async () => {
  const { container } = render(<GatewayRegisterPage />);
  fireEvent.submit(container.querySelector("form")!);
  await waitFor(() => {
    expect(screen.getByText("Name is required.")).toBeTruthy();
  });
  expect(container.querySelector(".alert.alert-danger")).toBeTruthy();
});
