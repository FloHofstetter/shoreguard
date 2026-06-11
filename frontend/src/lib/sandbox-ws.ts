/**
 * Live sandbox event streaming over WebSocket with auto-reconnect.
 *
 * Events are re-dispatched as document CustomEvents so any island on
 * the page can subscribe independently:
 *   sg:ws-state             {sandboxName, state}
 *   sg:sandbox-status       {sandboxName, ...status}
 *   sg:approvals-update     {sandboxName, ...draft summary}
 *   sg:policy-status-update {sandboxName, draft_version}
 *   sg:sandbox-log          {sandboxName, kind: "log"|"event", ...data}
 */

import { CONFIG, GW, gwUrl, navigateTo } from "./constants";
import { showToast } from "./notify";

const activeSockets: Record<string, WebSocket> = {};
const reconnectState: Record<string, { attempts: number; sandboxId: string }> = {};

export interface SandboxEvent {
  type: string;
  data?: Record<string, unknown>;
}

function dispatch(name: string, detail: Record<string, unknown>): void {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

export function connectSandboxWebSocket(sandboxName: string, sandboxId: string): void {
  if (activeSockets[sandboxName]) {
    activeSockets[sandboxName].close();
  }
  reconnectState[sandboxName] = { attempts: 0, sandboxId };
  doConnect(sandboxName);
}

export function disconnectSandboxWebSocket(sandboxName: string): void {
  delete reconnectState[sandboxName];
  activeSockets[sandboxName]?.close();
  delete activeSockets[sandboxName];
}

function doConnect(sandboxName: string): void {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${window.location.host}/ws/${GW}/${sandboxName}`);
  let heartbeatTimer: number | undefined;

  const resetHeartbeatTimer = () => {
    clearTimeout(heartbeatTimer);
    heartbeatTimer = window.setTimeout(() => {
      console.warn(`WebSocket: heartbeat timeout for ${sandboxName}`);
      ws.close();
    }, CONFIG.wsHeartbeatTimeout);
  };

  ws.onopen = () => {
    resetHeartbeatTimer();
    dispatch("sg:ws-state", { sandboxName, state: "connected" });
  };

  ws.onmessage = (event) => {
    let data: SandboxEvent;
    try {
      data = JSON.parse(event.data);
    } catch {
      console.warn("WebSocket: failed to parse message", event.data);
      return;
    }
    resetHeartbeatTimer();
    if (reconnectState[sandboxName]) reconnectState[sandboxName].attempts = 0;
    if (data.type === "heartbeat") return;
    handleEvent(sandboxName, data);
  };

  ws.onclose = () => {
    clearTimeout(heartbeatTimer);
    delete activeSockets[sandboxName];
    dispatch("sg:ws-state", { sandboxName, state: "disconnected" });

    // Reconnect only while still on a sandbox detail page.
    if (window.location.pathname.includes("/sandboxes/")) {
      const state = reconnectState[sandboxName];
      if (!state) return;
      state.attempts++;
      if (state.attempts > CONFIG.wsMaxRetries) {
        console.error(`WebSocket: max retries reached for ${sandboxName}`);
        dispatch("sg:ws-state", { sandboxName, state: "failed" });
        showToast(`Live updates unavailable for ${sandboxName}. Refresh to retry.`, "warning");
        return;
      }
      dispatch("sg:ws-state", { sandboxName, state: "reconnecting" });
      const backoff = Math.min(1000 * Math.pow(2, state.attempts - 1), CONFIG.wsMaxBackoff);
      setTimeout(() => {
        if (window.location.pathname.includes("/sandboxes/")) {
          doConnect(sandboxName);
        }
      }, backoff);
    }
  };

  ws.onerror = () => {
    const state = reconnectState[sandboxName];
    if (state && state.attempts === 0) {
      showToast(`WebSocket error for ${sandboxName}`, "warning");
    }
  };

  activeSockets[sandboxName] = ws;
}

function handleEvent(sandboxName: string, event: SandboxEvent): void {
  const data = event.data ?? {};

  if (event.type === "status" && event.data) {
    dispatch("sg:sandbox-status", { sandboxName, ...data });
    if (data.phase === "error") {
      showToast(`Sandbox ${sandboxName} entered error state.`, "danger");
    }
  }

  if (event.type === "draft_policy_update") {
    dispatch("sg:policy-status-update", { sandboxName, draft_version: data.draft_version });
    if (typeof data.total_pending === "number" && data.total_pending > 0) {
      const summary = typeof data.summary === "string" && data.summary ? ` ${data.summary}` : "";
      showToast(`${sandboxName}: ${data.total_pending} pending approval(s).${summary}`, "warning", {
        label: "Review",
        onClick: () => navigateTo(gwUrl(`/sandboxes/${sandboxName}`)),
      });
      dispatch("sg:approvals-update", { sandboxName, ...data });
    }
  }

  if (event.type === "log" || event.type === "event") {
    dispatch("sg:sandbox-log", { sandboxName, kind: event.type, ...data });
  }

  if (event.type === "warning") {
    showToast((data.message as string) || "Gateway warning", "warning");
  }
}
