/**
 * "Set up phone approvals" wizard (island).
 *
 * One button takes a solo operator from zero to "I can approve my agent from my
 * phone": subscribe this device to web push, wire a webpush webhook to the
 * approval events, and fire a sample approval notification to tap. Honest about
 * the requirement that the phone needs a reachable URL (LAN bind / Tailscale).
 */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth } from "../lib/auth";
import { currentSubscription, enablePush, pushSupported } from "../lib/push";

export const APPROVAL_EVENTS = [
  "approval.pending",
  "approval.escalated",
  "budget.exceeded",
  "kill_switch.engaged",
];

/** The exact POST body that wires a webpush webhook to the approval events. */
export function buildWebhookBody(): {
  channel_type: string;
  url: string;
  event_types: string[];
} {
  return { channel_type: "webpush", url: "webpush:all", event_types: APPROVAL_EVENTS };
}

interface Webhook {
  channel_type?: string;
  url?: string;
  event_types?: string[];
}

/** Reject after `ms` so a stalled push subscription can never hang the wizard. */
function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) =>
      window.setTimeout(
        () => reject(new Error("The push service didn't respond in time.")),
        ms,
      ),
    ),
  ]);
}

/** Whether the existing webhooks already include a webpush hook on approvals. */
export function hasApprovalWebpush(hooks: Webhook[]): boolean {
  return hooks.some(
    (w) =>
      (w.channel_type === "webpush" || w.url === "webpush:all") &&
      (w.event_types ?? []).includes("approval.pending"),
  );
}

type Status = "pending" | "running" | "done" | "error";

function StepRow({ status, label }: { status: Status; label: string }) {
  const icon =
    status === "done"
      ? "bi-check-circle-fill text-success"
      : status === "running"
        ? "bi-arrow-repeat text-primary"
        : status === "error"
          ? "bi-x-circle-fill text-danger"
          : "bi-circle text-muted";
  return (
    <li class="d-flex align-items-center gap-2 py-1">
      <i class={`bi ${icon}`} />
      <span class={status === "pending" ? "text-muted" : ""}>{label}</span>
    </li>
  );
}

export default function PhoneApprovalsSetup() {
  const [push, setPush] = useState<Status>("pending");
  const [hook, setHook] = useState<Status>("pending");
  const [test, setTest] = useState<Status>("pending");
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const [supported, setSupported] = useState(true);

  useEffect(() => {
    setSupported(pushSupported());
    void ensureAuth();
    // If this device is already subscribed, reflect that.
    void currentSubscription().then((s) => {
      if (s) setPush("done");
    });
  }, []);

  const run = async () => {
    setError("");
    setDone(false);
    setRunning(true);

    // Step 1 — subscribe this device to web push.
    setPush("running");
    if (!pushSupported()) {
      setPush("error");
      setError(
        "This browser can't do web push — it needs a secure context (localhost or HTTPS, " +
          "e.g. `tailscale serve`).",
      );
      setRunning(false);
      return;
    }
    try {
      await withTimeout(enablePush(), 20000);
      setPush("done");
    } catch (e) {
      setPush("error");
      setError(`${(e as Error).message} Allow notifications and try again.`);
      setRunning(false);
      return;
    }

    // Step 2 — wire a webpush webhook to the approval events (idempotent).
    setHook("running");
    try {
      const existing = await apiFetch<{ items?: Webhook[] } | Webhook[]>(`/api/webhooks`).catch(
        () => null,
      );
      const list = Array.isArray(existing) ? existing : (existing?.items ?? []);
      if (!hasApprovalWebpush(list)) {
        await apiFetch(`/api/webhooks`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildWebhookBody()),
        });
      }
      setHook("done");
    } catch (e) {
      setHook("error");
      setError(`Could not wire the notification webhook: ${(e as Error).message}`);
      setRunning(false);
      return;
    }

    // Step 3 — fire a sample approval the operator taps on their phone.
    setTest("running");
    try {
      await apiFetch(`/api/push/test-approval`, { method: "POST" });
      setTest("done");
      setDone(true);
    } catch (e) {
      setTest("error");
      setError(`Test notification failed: ${(e as Error).message}`);
    }
    setRunning(false);
  };

  return (
    <div class="card sg-card-themed mx-auto" style={{ maxWidth: "34rem" }}>
      <div class="card-body">
        <h5 class="mb-1">
          <i class="bi bi-phone me-2" />
          Set up phone approvals
        </h5>
        <p class="text-muted small">
          Get a push notification when an agent needs a decision, and approve or reject it from your
          phone — even while you're away.
        </p>

        <ul class="list-unstyled mb-3">
          <StepRow status={push} label="Enable notifications on this device" />
          <StepRow status={hook} label="Wire approval events to web push" />
          <StepRow status={test} label="Send a sample approval to tap" />
        </ul>

        {!supported && (
          <div class="alert alert-info py-2 small">
            This browser/context can't subscribe to web push. Open ShoreGuard over{" "}
            <code>localhost</code> or HTTPS (e.g. <code>tailscale serve</code>) on the device you
            want notified.
          </div>
        )}

        {error && <div class="alert alert-danger py-2 small mb-2">{error}</div>}

        {done && (
          <div class="alert alert-success py-2 small mb-2">
            Sent! Tap the notification on this device to confirm it opens your{" "}
            <a href="/approvals" class="alert-link">
              approval inbox
            </a>
            . For your phone, make sure it can reach this URL (LAN bind or Tailscale).
          </div>
        )}

        <button class="btn btn-success" disabled={running} onClick={() => void run()}>
          {running ? "Setting up…" : done ? "Run again" : "Set up phone approvals"}
        </button>
      </div>
    </div>
  );
}
