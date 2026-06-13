/** Gateway registration form (island). */

import { useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { navigateTo } from "../lib/constants";
import { showToast } from "../lib/notify";

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve((reader.result as string).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export default function GatewayRegisterPage() {
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [scheme, setScheme] = useState("https");
  const [authMode, setAuthMode] = useState("mtls");
  const [gpu, setGpu] = useState(false);
  const [description, setDescription] = useState("");
  const [caFile, setCaFile] = useState<File | null>(null);
  const [certFile, setCertFile] = useState<File | null>(null);
  const [keyFile, setKeyFile] = useState<File | null>(null);
  const [labelRows, setLabelRows] = useState<{ key: string; val: string }[]>([]);
  const [newLabelKey, setNewLabelKey] = useState("");
  const [newLabelVal, setNewLabelVal] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [output, setOutput] = useState("");

  const addLabel = () => {
    const key = newLabelKey.trim();
    const val = newLabelVal.trim();
    if (!key) return;
    if (labelRows.some((r) => r.key === key)) return;
    if (labelRows.length >= 20) return;
    setLabelRows([...labelRows, { key, val }]);
    setNewLabelKey("");
    setNewLabelVal("");
  };

  const submit = async (e: Event) => {
    e.preventDefault();
    if (!name.trim()) {
      setOutput("Name is required.");
      return;
    }
    if (!endpoint.trim()) {
      setOutput("Endpoint is required.");
      return;
    }
    setSubmitting(true);
    setOutput("");
    try {
      const body: Record<string, unknown> = {
        name: name.trim(),
        endpoint: endpoint.trim(),
        scheme,
        auth_mode: authMode,
        metadata: { gpu },
      };
      const desc = description.trim();
      if (desc) body.description = desc;
      if (labelRows.length > 0) {
        body.labels = Object.fromEntries(labelRows.map((r) => [r.key, r.val]));
      }
      if (caFile) body.ca_cert = await readFileAsBase64(caFile);
      if (certFile) body.client_cert = await readFileAsBase64(certFile);
      if (keyFile) body.client_key = await readFileAsBase64(keyFile);

      await apiFetch(`/api/gateway/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      showToast(`Gateway "${body.name}" registered.`, "success");
      navigateTo("/gateways");
    } catch (err) {
      setOutput((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const filePicker = (label: string, setter: (f: File | null) => void, accept: string) => (
    <div class="mb-2">
      <label class="form-label small">{label}</label>
      <input
        type="file"
        class="form-control form-control-sm"
        accept={accept}
        onChange={(e) => setter((e.target as HTMLInputElement).files?.[0] ?? null)}
      />
    </div>
  );

  return (
    <div class="card sg-card-themed">
      <div class="card-body">
        <form noValidate onSubmit={(e) => void submit(e)}>
          <div class="row g-3">
            <div class="col-md-6">
              <label class="form-label">Name</label>
              <input
                type="text"
                class="form-control"
                placeholder="my-gateway"
                required
                value={name}
                onInput={(e) => setName((e.target as HTMLInputElement).value)}
              />
            </div>
            <div class="col-md-6">
              <label class="form-label">Endpoint</label>
              <input
                type="text"
                class="form-control"
                placeholder="10.0.0.5:8443"
                required
                value={endpoint}
                onInput={(e) => setEndpoint((e.target as HTMLInputElement).value)}
              />
              <div class="form-text">host:port of the OpenShell gateway</div>
            </div>
          </div>
          <div class="row g-3 mt-1">
            <div class="col-md-6">
              <label class="form-label">Scheme</label>
              <select
                class="form-select"
                value={scheme}
                onChange={(e) => setScheme((e.target as HTMLSelectElement).value)}
              >
                <option value="https">https (mTLS)</option>
                <option value="http">http (insecure)</option>
              </select>
            </div>
            <div class="col-md-6">
              <label class="form-label">Auth Mode</label>
              <select
                class="form-select"
                value={authMode}
                onChange={(e) => setAuthMode((e.target as HTMLSelectElement).value)}
              >
                <option value="mtls">mTLS</option>
                <option value="insecure">Insecure</option>
                <option value="cloudflare_jwt">Cloudflare JWT</option>
              </select>
            </div>
          </div>
          <div class="mt-3">
            <h6 class="text-muted mb-2">mTLS Certificates</h6>
            {filePicker("CA Certificate (PEM)", setCaFile, ".pem,.crt")}
            {filePicker("Client Certificate (PEM)", setCertFile, ".pem,.crt")}
            {filePicker("Client Key (PEM)", setKeyFile, ".pem,.key")}
          </div>
          <div class="form-check form-switch mt-3">
            <input
              class="form-check-input"
              type="checkbox"
              id="reg-gw-gpu"
              checked={gpu}
              onChange={(e) => setGpu((e.target as HTMLInputElement).checked)}
            />
            <label class="form-check-label" for="reg-gw-gpu">
              GPU passthrough
            </label>
          </div>
          <div class="mt-3">
            <label class="form-label">Description</label>
            <input
              type="text"
              class="form-control form-control-sm"
              placeholder="e.g. Production EU-West for ML team"
              maxLength={1000}
              value={description}
              onInput={(e) => setDescription((e.target as HTMLInputElement).value)}
            />
          </div>
          <div class="mt-3">
            <label class="form-label">Labels</label>
            {labelRows.length > 0 && (
              <div class="mb-2 d-flex flex-wrap gap-1">
                {labelRows.map((lbl) => (
                  <span
                    key={lbl.key}
                    class="badge text-bg-light border d-inline-flex align-items-center gap-1"
                  >
                    <span class="font-monospace">{lbl.key}</span>
                    <span class="text-muted">=</span>
                    <span>{lbl.val}</span>
                    <button
                      type="button"
                      class="btn-close btn-close-sm ms-1 sg-fs-xxs"
                      onClick={() => setLabelRows(labelRows.filter((r) => r.key !== lbl.key))}
                    />
                  </span>
                ))}
              </div>
            )}
            {labelRows.length < 20 && (
              <div class="input-group input-group-sm">
                <input
                  type="text"
                  class="form-control font-monospace"
                  placeholder="key"
                  value={newLabelKey}
                  onInput={(e) => setNewLabelKey((e.target as HTMLInputElement).value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addLabel();
                    }
                  }}
                />
                <span class="input-group-text">=</span>
                <input
                  type="text"
                  class="form-control"
                  placeholder="value"
                  value={newLabelVal}
                  onInput={(e) => setNewLabelVal((e.target as HTMLInputElement).value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addLabel();
                    }
                  }}
                />
                <button
                  class="btn btn-outline-success"
                  type="button"
                  onClick={addLabel}
                  disabled={!newLabelKey.trim()}
                >
                  <i class="bi bi-plus" />
                </button>
              </div>
            )}
          </div>

          {output && <div class="alert alert-danger py-1 small mt-2 mb-0">{output}</div>}

          <div class="d-flex gap-2 mt-3">
            <button type="submit" class="btn btn-success" disabled={submitting}>
              <i class="bi bi-plus me-1" />
              Register
            </button>
            <a href="/gateways" class="btn btn-outline-secondary">
              Cancel
            </a>
          </div>
        </form>
      </div>
    </div>
  );
}
