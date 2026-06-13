/**
 * One-click first-gateway bootstrap for a fresh local box.
 *
 * Shown only in local mode: a developer who just installed ShoreGuard on their
 * own PC (with OpenShell available) can spin up a local gateway from the empty
 * state instead of dropping to the CLI. Renders nothing when not in local mode,
 * so remote/production deployments are unaffected.
 */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { showToast } from "../lib/notify";

export function LocalGatewayCreate() {
  const [localMode, setLocalMode] = useState(false);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("openshell");
  const [gpu, setGpu] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch<{ local_mode?: boolean }>(`/api/gateway/local-inference`)
      .then((r) => setLocalMode(!!r?.local_mode))
      .catch(() => setLocalMode(false));
  }, []);

  if (!localMode) return null;

  const submit = async (e: Event) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Gateway name is required.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      await apiFetch(`/api/gateway/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), gpu }),
      });
      showToast(
        `Creating local gateway "${name.trim()}" — this can take a minute.`,
        "success",
      );
      setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button class="btn btn-success btn-sm" onClick={() => setOpen(true)}>
        <i class="bi bi-magic me-1" />
        Create local gateway
      </button>
    );
  }

  return (
    <div class="w-100">
      <form
        noValidate
        onSubmit={(e) => void submit(e)}
        class="text-start mx-auto mt-2"
        style={{ maxWidth: "24rem" }}
      >
        <label class="form-label small mb-1" for="lg-name">
          Gateway name
        </label>
        <input
          id="lg-name"
          class="form-control form-control-sm mb-2"
          value={name}
          required
          onInput={(e) => setName((e.target as HTMLInputElement).value)}
        />
        <div class="form-check mb-2">
          <input
            id="lg-gpu"
            class="form-check-input"
            type="checkbox"
            checked={gpu}
            onChange={(e) => setGpu((e.target as HTMLInputElement).checked)}
          />
          <label class="form-check-label small" for="lg-gpu">
            Enable GPU access
          </label>
        </div>
        {error && <div class="alert alert-danger py-1 small mb-2">{error}</div>}
        <div class="d-flex gap-2">
          <button class="btn btn-success btn-sm" type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </button>
          <button
            class="btn btn-outline-secondary btn-sm"
            type="button"
            disabled={busy}
            onClick={() => setOpen(false)}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
