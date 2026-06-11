import { describe, expect, it } from "vitest";

import { isLoopbackHostname, rewriteToCandidate } from "./phone-url";

describe("isLoopbackHostname", () => {
  it("detects loopback hostnames", () => {
    expect(isLoopbackHostname("localhost")).toBe(true);
    expect(isLoopbackHostname("127.0.0.1")).toBe(true);
    expect(isLoopbackHostname("[::1]")).toBe(true);
    expect(isLoopbackHostname("foo.localhost")).toBe(true);
  });

  it("passes through reachable hostnames", () => {
    expect(isLoopbackHostname("192.168.178.28")).toBe(false);
    expect(isLoopbackHostname("shoreguard.tailnet-1234.ts.net")).toBe(false);
  });
});

describe("rewriteToCandidate", () => {
  it("keeps path, query, and hash while swapping the origin", () => {
    const out = rewriteToCandidate(
      "http://localhost:8888/gateways/spark?tab=policies#top",
      "http://192.168.178.28:8888/",
    );
    expect(out).toBe("http://192.168.178.28:8888/gateways/spark?tab=policies#top");
  });

  it("adopts the candidate's port and scheme", () => {
    const out = rewriteToCandidate(
      "http://localhost:8888/dashboard",
      "https://spark.tailnet-1234.ts.net/",
    );
    expect(out).toBe("https://spark.tailnet-1234.ts.net/dashboard");
  });
});
