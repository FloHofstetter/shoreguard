/** Web Push enrolment helpers for the installed PWA.
 *
 * Flow: register the service worker (served at /sw.js so its scope is
 * the whole app), ask for notification permission, subscribe with the
 * server's VAPID public key, and POST the subscription. Requires a
 * secure context (HTTPS or localhost) — `tailscale serve` provides
 * exactly that in the homelab.
 */

import { apiFetch } from "./api";

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && window.isSecureContext;
}

/** Return the current subscription on this device, if any. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration("/");
  return reg ? await reg.pushManager.getSubscription() : null;
}

/** Enable push on this device; resolves when the server stored the subscription. */
export async function enablePush(): Promise<void> {
  if (!pushSupported()) {
    throw new Error("Push needs a secure context (HTTPS or localhost) and a modern browser.");
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Notification permission was not granted.");
  }
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const { public_key } = await apiFetch<{ public_key: string }>(`/api/push/public-key`);
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(public_key) as BufferSource,
    }));
  await apiFetch(`/api/push/subscriptions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sub.toJSON()),
  });
}

/** Disable push on this device (unsubscribes locally and server-side). */
export async function disablePush(): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  const endpoint = sub.endpoint;
  await sub.unsubscribe();
  await apiFetch(`/api/push/subscriptions?endpoint=${encodeURIComponent(endpoint)}`, {
    method: "DELETE",
  }).catch(() => undefined);
}

/** Ask the server to send a test notification to this user's devices. */
export async function sendTestPush(): Promise<{ sent: number }> {
  return apiFetch<{ sent: number }>(`/api/push/test`, { method: "POST" });
}
