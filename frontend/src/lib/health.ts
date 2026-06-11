/** Gateway health polling for the app shell (port of the Alpine store). */

import { signal } from "@preact/signals";

import { CONFIG, GW } from "./constants";
import { showToast } from "./notify";

export interface HealthState {
  connected: boolean;
  status: "unknown" | "connected" | "degraded" | "disconnected";
  version: string;
}

export const health = signal<HealthState>({ connected: false, status: "unknown", version: "" });

let interval: number | undefined;
let initialCheck = true;

export async function checkHealth(): Promise<void> {
  if (!GW) return;
  try {
    const resp = await fetch(`/api/gateways/${GW}/health`);
    if (!resp.ok) throw new Error("Degraded");
    const data = await resp.json();
    const wasConnected = health.value.connected;
    health.value = { connected: true, status: "connected", version: data.version || "" };
    if (!wasConnected) {
      clearInterval(interval);
      interval = window.setInterval(() => void checkHealth(), CONFIG.healthCheckInterval);
      if (!initialCheck) showToast("Gateway connected.", "success");
    }
    initialCheck = false;
  } catch (e) {
    const wasDegraded = (e as Error).message === "Degraded";
    const wasConnected = health.value.connected;
    health.value = {
      connected: false,
      status: wasDegraded ? "degraded" : "disconnected",
      version: health.value.version,
    };
    if (wasConnected) {
      clearInterval(interval);
      interval = window.setInterval(() => void checkHealth(), CONFIG.healthCheckFallback);
    }
  }
}

export function startHealthPolling(): void {
  if (!GW) return;
  void checkHealth();
  interval = window.setInterval(() => void checkHealth(), CONFIG.healthCheckInterval);
}
