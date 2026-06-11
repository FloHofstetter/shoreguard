/** One-tap approval confirmation page (island, mobile-first). */

import { useState } from "preact/hooks";

import { apiFetch } from "../lib/api";

interface OneTapProps {
  token: string;
  gateway: string;
  sandbox: string;
  chunk_id: string;
  decision: "approve" | "reject";
}

interface VoteResult {
  status: string;
  votes?: number;
  needed?: number;
}

export default function OneTapApproval(props: OneTapProps) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<VoteResult | null>(null);
  const [error, setError] = useState("");

  const isApprove = props.decision === "approve";

  const cast = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await apiFetch<VoteResult>(`/api/approvals/one-tap`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: props.token }),
      });
      setResult(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    const pending = result.status === "pending";
    return (
      <div class="text-center py-4">
        <i
          class={`bi ${
            pending
              ? "bi-hourglass-split text-warning"
              : isApprove
                ? "bi-check-circle text-success"
                : "bi-x-circle text-danger"
          } display-4`}
        />
        <h5 class="mt-3">
          {pending
            ? `Vote recorded — ${result.votes}/${result.needed} approvals`
            : result.status === "approved"
              ? "Approved"
              : "Rejected"}
        </h5>
        <div class="text-muted small">
          {props.sandbox} on {props.gateway}
        </div>
      </div>
    );
  }

  return (
    <div class="text-center py-3">
      <i class={`bi ${isApprove ? "bi-check-circle" : "bi-x-circle"} display-4 text-muted`} />
      <h5 class="mt-3">{isApprove ? "Approve" : "Reject"} policy change?</h5>
      <div class="mb-3">
        <div>
          Sandbox <strong>{props.sandbox}</strong> on gateway <strong>{props.gateway}</strong>
        </div>
        <div class="text-muted small font-monospace">chunk {props.chunk_id}</div>
      </div>
      {error && <div class="alert alert-danger small">{error}</div>}
      <button
        class={`btn btn-lg w-100 ${isApprove ? "btn-success" : "btn-danger"}`}
        disabled={busy}
        onClick={() => void cast()}
      >
        {busy ? (
          <span class="spinner-border spinner-border-sm me-2" />
        ) : (
          <i class={`bi ${isApprove ? "bi-check-lg" : "bi-x-lg"} me-2`} />
        )}
        Confirm {isApprove ? "approval" : "rejection"}
      </button>
    </div>
  );
}
