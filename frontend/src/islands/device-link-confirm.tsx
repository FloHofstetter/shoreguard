/**
 * Phone side of the QR device-link handoff (the /login/device page).
 *
 * Reads the one-time code from the URL fragment (never sent to the
 * server), immediately strips it from history, then polls the redeem
 * endpoint: it shows whose account it is signing into, waits for the
 * operator to approve on their other device, and redirects once a
 * session has been minted.
 */

import { useEffect, useRef, useState } from "preact/hooks";

type Status = "loading" | "pending" | "approved" | "denied" | "expired" | "invalid" | "consumed";

interface RedeemResponse {
  status: Exclude<Status, "loading">;
  email?: string | null;
}

const POLL_MS = 2000;

function readCodeFromHash(): string {
  // The code lives in the fragment so it never reaches the server or
  // its logs. Strip it from the address bar / history right away so a
  // live code does not linger if the user navigates back.
  const hash = window.location.hash.replace(/^#/, "");
  if (hash) {
    history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return hash;
}

export default function DeviceLinkConfirm() {
  const [status, setStatus] = useState<Status>("loading");
  const [email, setEmail] = useState<string | null>(null);
  const codeRef = useRef<string>("");

  useEffect(() => {
    codeRef.current = readCodeFromHash();
    if (!codeRef.current) {
      setStatus("invalid");
      return;
    }
    let active = true;
    let timer = 0;

    const poll = async () => {
      try {
        const resp = await fetch("/api/auth/device-link/redeem", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: codeRef.current }),
        });
        const data = (await resp.json()) as RedeemResponse;
        if (!active) return;
        if (data.email) setEmail(data.email);
        setStatus(data.status);
        if (data.status === "approved") {
          window.location.href = "/";
          return;
        }
        if (data.status === "pending") {
          timer = window.setTimeout(poll, POLL_MS);
        }
      } catch {
        if (active) timer = window.setTimeout(poll, POLL_MS);
      }
    };
    poll();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, []);

  return (
    <div class="text-center p-2">
      <i class="bi bi-phone fs-1 sg-text-accent" />
      <h4 class="mt-2 mb-3">Sign in on this device</h4>
      <Body status={status} email={email} />
    </div>
  );
}

function Body({ status, email }: { status: Status; email: string | null }) {
  if (status === "loading") {
    return (
      <p class="text-muted">
        <span class="spinner-border spinner-border-sm me-2" />
        Reading sign-in code…
      </p>
    );
  }
  if (status === "pending") {
    return (
      <>
        <p class="mb-1">
          Signing in as{" "}
          <strong>{email ?? "…"}</strong>
        </p>
        <p class="text-muted small mb-3">
          Approve this request on the device that showed the QR code. If you did not start
          this, just close the page.
        </p>
        <div class="d-flex justify-content-center align-items-center gap-2 text-muted">
          <span class="spinner-border spinner-border-sm" />
          Waiting for approval…
        </div>
      </>
    );
  }
  if (status === "approved") {
    return (
      <p class="text-success">
        <i class="bi bi-check-circle me-1" />
        Approved — signing you in…
      </p>
    );
  }
  const messages: Record<string, { icon: string; text: string }> = {
    denied: { icon: "x-octagon", text: "The request was denied on the other device." },
    expired: { icon: "clock-history", text: "This sign-in code has expired. Generate a new one." },
    invalid: { icon: "question-octagon", text: "This sign-in link is not valid." },
    consumed: { icon: "check2-all", text: "This sign-in code has already been used." },
  };
  const m = messages[status] ?? messages.invalid;
  return (
    <div class="alert alert-secondary">
      <i class={`bi bi-${m.icon} me-1`} />
      {m.text}
      <div class="mt-2">
        <a href="/login" class="btn btn-sm btn-outline-secondary">
          Back to login
        </a>
      </div>
    </div>
  );
}
