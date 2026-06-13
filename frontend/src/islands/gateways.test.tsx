/** Unit tests for the honest transport label (plaintext vs stored auth_mode). */

import { expect, test } from "vitest";

import { transportLabel } from "./gateways";

test("transportLabel reports plaintext for an http/grpc scheme regardless of auth_mode", () => {
  expect(transportLabel({ scheme: "http", endpoint: "1.2.3.4:9443", auth_mode: "mtls" })).toBe(
    "plaintext",
  );
  expect(transportLabel({ scheme: "grpc", auth_mode: "mtls" })).toBe("plaintext");
});

test("transportLabel reports plaintext when the endpoint is an http:// URL", () => {
  expect(transportLabel({ endpoint: "http://127.0.0.1:9443", auth_mode: "mtls" })).toBe(
    "plaintext",
  );
});

test("transportLabel shows the stored auth_mode for secure transports", () => {
  expect(transportLabel({ scheme: "https", auth_mode: "mtls" })).toBe("mtls");
  expect(transportLabel({ scheme: "https", auth_mode: "api_key" })).toBe("api_key");
  expect(transportLabel({ scheme: "https" })).toBe("—");
});
