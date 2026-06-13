/** Service routing page (island): list/expose/delete gateway endpoints. */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth, hasRole } from "../lib/auth";
import { API } from "../lib/constants";
import { Modal } from "../lib/Modal";
import { showConfirm, showToast } from "../lib/notify";
import { EmptyState, ErrorAlert, Spinner } from "../lib/widgets";

interface Service {
  service_name: string;
  sandbox_name: string;
  target_port: number;
  url?: string;
}

function ExposeModal({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const [sandbox, setSandbox] = useState("");
  const [service, setService] = useState("");
  const [port, setPort] = useState("");
  const [domain, setDomain] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: Event) => {
    e.preventDefault();
    const targetPort = parseInt(port, 10);
    if (!sandbox.trim()) {
      setError("Sandbox is required.");
      return;
    }
    if (!service.trim()) {
      setError("Service name is required.");
      return;
    }
    if (!targetPort) {
      setError("A target port is required.");
      return;
    }
    setError("");
    try {
      await apiFetch(`${API}/services`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sandbox: sandbox.trim(),
          service: service.trim(),
          target_port: targetPort,
          domain,
        }),
      });
      showToast(`Service "${service.trim()}" exposed.`, "success");
      onDone();
    } catch (err) {
      showToast(`Expose failed: ${(err as Error).message}`, "danger");
    }
  };

  return (
    <Modal
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-hdd-network me-2" />
          Expose service
        </span>
      }
    >
      <form class="row g-2" noValidate onSubmit={(e) => void submit(e)}>
        <div class="col-md-6">
          <label class="form-label small">Sandbox</label>
          <input
            class="form-control form-control-sm"
            required
            value={sandbox}
            onInput={(e) => setSandbox((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="col-md-6">
          <label class="form-label small">Service name</label>
          <input
            class="form-control form-control-sm"
            required
            value={service}
            onInput={(e) => setService((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="col-md-6">
          <label class="form-label small">Target port</label>
          <input
            type="number"
            min={1}
            max={65535}
            class="form-control form-control-sm"
            required
            value={port}
            onInput={(e) => setPort((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="col-md-6 d-flex align-items-end">
          <div class="form-check">
            <input
              class="form-check-input"
              type="checkbox"
              id="expose-domain"
              checked={domain}
              onChange={(e) => setDomain((e.target as HTMLInputElement).checked)}
            />
            <label class="form-check-label small" for="expose-domain">
              Browser-facing URL (domain)
            </label>
          </div>
        </div>
        {error && (
          <div class="col-12">
            <div class="alert alert-danger py-1 small mb-0">{error}</div>
          </div>
        )}
        <div class="col-12 text-end mt-3">
          <button type="submit" class="btn btn-success btn-sm">
            <i class="bi bi-plus-lg me-1" />
            Expose
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default function ServicesPage() {
  const [services, setServices] = useState<Service[] | null>(null);
  const [error, setError] = useState("");
  const [isOperator, setIsOperator] = useState(false);
  const [exposeOpen, setExposeOpen] = useState(false);

  const load = async () => {
    setError("");
    try {
      const resp = await apiFetch<{ services?: Service[] }>(`${API}/services`);
      setServices(resp?.services ?? []);
    } catch (e) {
      setError((e as Error).message);
      setServices([]);
    }
  };

  useEffect(() => {
    void load();
    void ensureAuth().then(() => setIsOperator(hasRole("operator")));
  }, []);

  const deleteService = async (sandbox: string, service: string) => {
    const confirmed = await showConfirm(`Delete service routing for "${sandbox}/${service}"?`, {
      icon: "trash3",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!confirmed) return;
    try {
      await apiFetch(
        `${API}/services/${encodeURIComponent(sandbox)}/${encodeURIComponent(service)}`,
        { method: "DELETE" },
      );
      showToast(`Service "${service}" deleted.`, "success");
      await load();
    } catch (e) {
      showToast(`Delete failed: ${(e as Error).message}`, "danger");
    }
  };

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">Exposed Services</h5>
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-secondary" onClick={() => void load()} title="Refresh">
            <i class="bi bi-arrow-clockwise" />
          </button>
          {isOperator && (
            <button class="btn btn-outline-success" onClick={() => setExposeOpen(true)}>
              <i class="bi bi-plus-lg me-1" />
              Expose service
            </button>
          )}
        </div>
      </div>
      <p class="text-muted small">
        Publish a loopback port inside a sandbox on a gateway-routed endpoint. Enable{" "}
        <strong>domain</strong> for a browser-facing URL.
      </p>

      {services === null && <Spinner message="Loading services…" />}
      {error && <ErrorAlert message={error} />}
      {services !== null && !error && services.length === 0 && (
        <EmptyState icon="hdd-network" message="No services exposed on this gateway yet." />
      )}
      {services !== null && services.length > 0 && (
        <div class="table-responsive">
          <table class="table table-striped table-hover table-sm align-middle">
            <thead>
              <tr>
                <th>Service</th>
                <th>Sandbox</th>
                <th>Target port</th>
                <th>URL</th>
                <th class="text-end">Actions</th>
              </tr>
            </thead>
            <tbody>
              {services.map((s) => (
                <tr key={`${s.sandbox_name}/${s.service_name}`}>
                  <td>
                    <strong>{s.service_name}</strong>
                  </td>
                  <td class="small text-muted">{s.sandbox_name}</td>
                  <td>
                    <code class="small">{s.target_port}</code>
                  </td>
                  <td>
                    {s.url ? (
                      <a href={s.url} target="_blank" rel="noopener" class="font-monospace small">
                        {s.url}
                      </a>
                    ) : (
                      <span class="text-muted">—</span>
                    )}
                  </td>
                  <td class="text-end">
                    {isOperator && (
                      <button
                        class="btn btn-sm text-muted"
                        title="Delete"
                        onClick={() => void deleteService(s.sandbox_name, s.service_name)}
                      >
                        <i class="bi bi-trash3" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {exposeOpen && (
        <ExposeModal
          onDone={() => {
            setExposeOpen(false);
            void load();
          }}
          onClose={() => setExposeOpen(false)}
        />
      )}
    </div>
  );
}
