/** Passkey (WebAuthn) flows against the ShoreGuard API.
 *
 * Uses the native JSON conversions (`parseCreationOptionsFromJSON`,
 * `credential.toJSON()`) when available — all evergreen browsers ship
 * them — with manual base64url fallbacks for older ones. Requires a
 * secure context (HTTPS or localhost).
 */

import { apiFetch } from "./api";

export function passkeysSupported(): boolean {
  return typeof PublicKeyCredential !== "undefined" && window.isSecureContext;
}

/** POST for the anonymous login pair — unlike apiFetch, a 401 here is a
 * normal "wrong passkey" outcome that must surface as an error message,
 * not a hard redirect to /login (which would poison `next=`). */
async function postAnonymous<T>(url: string, body: unknown): Promise<T> {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp
      .json()
      .then((d) => d.detail)
      .catch(() => undefined);
    throw new Error(typeof detail === "string" ? detail : `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

function b64urlToBuf(value: string): ArrayBuffer {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0)).buffer;
}

function bufToB64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let str = "";
  for (const b of bytes) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/* eslint-disable @typescript-eslint/no-explicit-any */

function parseCreationOptions(json: any): PublicKeyCredentialCreationOptions {
  const pkc = PublicKeyCredential as any;
  if (pkc.parseCreationOptionsFromJSON) return pkc.parseCreationOptionsFromJSON(json);
  return {
    ...json,
    challenge: b64urlToBuf(json.challenge),
    user: { ...json.user, id: b64urlToBuf(json.user.id) },
    excludeCredentials: (json.excludeCredentials ?? []).map((c: any) => ({
      ...c,
      id: b64urlToBuf(c.id),
    })),
  };
}

function parseRequestOptions(json: any): PublicKeyCredentialRequestOptions {
  const pkc = PublicKeyCredential as any;
  if (pkc.parseRequestOptionsFromJSON) return pkc.parseRequestOptionsFromJSON(json);
  return {
    ...json,
    challenge: b64urlToBuf(json.challenge),
    allowCredentials: (json.allowCredentials ?? []).map((c: any) => ({
      ...c,
      id: b64urlToBuf(c.id),
    })),
  };
}

function credentialToJSON(cred: PublicKeyCredential): unknown {
  const anyCred = cred as any;
  if (typeof anyCred.toJSON === "function") return anyCred.toJSON();
  const resp = cred.response as any;
  const out: any = {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    clientExtensionResults: cred.getClientExtensionResults(),
    response: { clientDataJSON: bufToB64url(resp.clientDataJSON) },
  };
  if (resp.attestationObject) {
    out.response.attestationObject = bufToB64url(resp.attestationObject);
    if (typeof resp.getTransports === "function") out.response.transports = resp.getTransports();
  } else {
    out.response.authenticatorData = bufToB64url(resp.authenticatorData);
    out.response.signature = bufToB64url(resp.signature);
    if (resp.userHandle) out.response.userHandle = bufToB64url(resp.userHandle);
  }
  return out;
}

/* eslint-enable @typescript-eslint/no-explicit-any */

/** Register a new passkey for the logged-in user. */
export async function registerPasskey(name: string): Promise<void> {
  if (!passkeysSupported()) {
    throw new Error("Passkeys need HTTPS (or localhost) and a modern browser.");
  }
  const { options, state } = await apiFetch<{ options: unknown; state: string }>(
    `/api/auth/passkeys/register/options`,
    { method: "POST" },
  );
  const cred = (await navigator.credentials.create({
    publicKey: parseCreationOptions(options),
  })) as PublicKeyCredential | null;
  if (!cred) throw new Error("Passkey creation was cancelled.");
  await apiFetch(`/api/auth/passkeys/register/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state, credential: credentialToJSON(cred), name }),
  });
}

/** Sign in with a passkey; resolves with the session info on success. */
export async function loginWithPasskey(): Promise<{ role: string; email: string }> {
  if (!passkeysSupported()) {
    throw new Error("Passkeys need HTTPS (or localhost) and a modern browser.");
  }
  const { options, state } = await postAnonymous<{ options: unknown; state: string }>(
    `/api/auth/login/passkey/options`,
    undefined,
  );
  const cred = (await navigator.credentials.get({
    publicKey: parseRequestOptions(options),
  })) as PublicKeyCredential | null;
  if (!cred) throw new Error("Passkey sign-in was cancelled.");
  return postAnonymous<{ role: string; email: string }>(`/api/auth/login/passkey/verify`, {
    state,
    credential: credentialToJSON(cred),
  });
}
