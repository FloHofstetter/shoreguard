/** Webhooks management page (island). */

import { useEffect, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { Modal } from "../lib/Modal";
import { showConfirm, showToast } from "../lib/notify";
import { useSortableTable } from "../lib/table";
import { EmptyState, ErrorAlert, Spinner } from "../lib/widgets";

interface Webhook extends Record<string, unknown> {
  id: string;
  url: string;
  channel_type?: string;
  event_types?: string[];
  is_active: boolean;
  created_at?: string | null;
}

interface Delivery {
  id: string;
  status: string;
  event_type?: string;
  response_code?: number;
  attempt?: number;
  created_at?: string | null;
  delivered_at?: string | null;
  error_message?: string;
}

const CHANNELS = ["generic", "slack", "discord", "email", "ntfy", "telegram", "mqtt"];

function formatDateTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

function splitCsv(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

function ChannelSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select
      class="form-select form-select-sm"
      value={value}
      onChange={(e) => onChange((e.target as HTMLSelectElement).value)}
    >
      {CHANNELS.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  );
}

function EditWebhookModal({ webhook, onSaved, onClose }: {
  webhook: Webhook;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState(webhook.url);
  const [channel, setChannel] = useState(webhook.channel_type || "generic");
  const [events, setEvents] = useState((webhook.event_types || []).join(", "));
  const [error, setError] = useState("");

  const save = async () => {
    setError("");
    const eventList = splitCsv(events);
    if (!url.trim()) {
      setError("URL is required.");
      return;
    }
    if (eventList.length === 0) {
      setError("At least one event type is required.");
      return;
    }
    try {
      await apiFetch(`/api/webhooks/${webhook.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim(), event_types: eventList, channel_type: channel }),
      });
      showToast(`Webhook ${webhook.id} updated.`, "success");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Modal
      size="lg"
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-pencil me-2" />
          Edit Webhook <code class="ms-2 small text-muted">{webhook.id}</code>
        </span>
      }
      footer={
        <button class="btn btn-sm btn-success" onClick={() => void save()}>
          <i class="bi bi-check-lg me-1" />
          Save
        </button>
      }
    >
      {error && <div class="alert alert-danger py-1 small">{error}</div>}
      <div class="mb-2">
        <label class="form-label small text-muted mb-1">Target URL</label>
        <input
          type="text"
          class="form-control form-control-sm"
          value={url}
          onInput={(e) => setUrl((e.target as HTMLInputElement).value)}
        />
      </div>
      <div class="mb-2">
        <label class="form-label small text-muted mb-1">Channel</label>
        <ChannelSelect value={channel} onChange={setChannel} />
      </div>
      <div class="mb-2">
        <label class="form-label small text-muted mb-1">Event types (comma-separated)</label>
        <input
          type="text"
          class="form-control form-control-sm"
          value={events}
          onInput={(e) => setEvents((e.target as HTMLInputElement).value)}
        />
      </div>
    </Modal>
  );
}

function DeliveriesModal({ webhook, onClose }: { webhook: Webhook; onClose: () => void }) {
  const [deliveries, setDeliveries] = useState<Delivery[] | null>(null);

  useEffect(() => {
    apiFetch<Delivery[]>(`/api/webhooks/${webhook.id}/deliveries?limit=100`)
      .then(setDeliveries)
      .catch((e: Error) => {
        showToast(`Failed to load deliveries: ${e.message}`, "danger");
        setDeliveries([]);
      });
  }, [webhook.id]);

  const statusBadge = (status: string) =>
    status === "success"
      ? "text-bg-success"
      : status === "failed"
        ? "text-bg-danger"
        : "text-bg-warning";

  return (
    <Modal
      size="lg"
      onClose={onClose}
      title={
        <span>
          <i class="bi bi-list-check me-2" />
          Delivery log <code class="ms-2 small text-muted">{webhook.id}</code>
          <span class="text-muted small ms-1">{webhook.url}</span>
        </span>
      }
    >
      {deliveries === null && (
        <div class="text-center text-muted py-3">
          <span class="spinner-border spinner-border-sm me-1" />
          Loading…
        </div>
      )}
      {deliveries !== null && deliveries.length > 0 && (
        <div class="table-responsive">
          <table class="table table-sm table-striped align-middle">
            <thead>
              <tr>
                <th>Status</th>
                <th>Event</th>
                <th>Code</th>
                <th>Attempt</th>
                <th>Created</th>
                <th>Delivered</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {deliveries.map((d) => (
                <tr key={d.id}>
                  <td>
                    <span class={`badge ${statusBadge(d.status)}`}>{d.status}</span>
                  </td>
                  <td>
                    <code class="small">{d.event_type || "—"}</code>
                  </td>
                  <td>{d.response_code || "—"}</td>
                  <td>{d.attempt || "—"}</td>
                  <td class="small text-muted">{formatDateTime(d.created_at)}</td>
                  <td class="small text-muted">{formatDateTime(d.delivered_at)}</td>
                  <td class="small text-danger text-truncate sg-mw-200" title={d.error_message}>
                    {d.error_message || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {deliveries !== null && deliveries.length === 0 && (
        <p class="text-muted small mb-0">No delivery attempts recorded yet.</p>
      )}
    </Modal>
  );
}

export default function WebhooksPage() {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const table = useSortableTable<Webhook>("id", "desc");

  const [showCreate, setShowCreate] = useState(false);
  const [newUrl, setNewUrl] = useState("");
  const [newEvents, setNewEvents] = useState("");
  const [newChannel, setNewChannel] = useState("generic");
  const [newSmtpHost, setNewSmtpHost] = useState("");
  const [newToAddrs, setNewToAddrs] = useState("");
  const [newNtfyToken, setNewNtfyToken] = useState("");
  const [newMqttTopic, setNewMqttTopic] = useState("");
  const [createError, setCreateError] = useState("");
  const [lastCreated, setLastCreated] = useState<{
    id: string;
    url: string;
    secret: string;
  } | null>(null);

  const [editing, setEditing] = useState<Webhook | null>(null);
  const [deliveriesFor, setDeliveriesFor] = useState<Webhook | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await apiFetch<{ items?: Webhook[] }>(`/api/webhooks`);
      setWebhooks(resp?.items ?? []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const createWebhook = async () => {
    setCreateError("");
    const url = newUrl.trim();
    const events = splitCsv(newEvents);
    if (!url) {
      setCreateError("URL is required.");
      return;
    }
    if (events.length === 0) {
      setCreateError("At least one event type is required.");
      return;
    }
    const body: Record<string, unknown> = {
      url,
      event_types: events,
      channel_type: newChannel,
    };
    if (newChannel === "email") {
      const host = newSmtpHost.trim();
      const to = splitCsv(newToAddrs);
      if (to.length === 0) {
        setCreateError("Email channel needs at least one to-address.");
        return;
      }
      body.extra_config = host ? { smtp_host: host, to_addrs: to } : { to_addrs: to };
    }
    if (newChannel === "ntfy" && newNtfyToken.trim()) {
      body.extra_config = { token: newNtfyToken.trim() };
    }
    if (newChannel === "mqtt" && newMqttTopic.trim()) {
      body.extra_config = { topic: newMqttTopic.trim() };
    }
    try {
      const created = await apiFetch<{ id: string; url: string; secret: string }>(
        `/api/webhooks`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      setLastCreated({ id: created.id, url: created.url, secret: created.secret });
      showToast(`Webhook ${created.id} created.`, "success");
      setNewUrl("");
      setNewEvents("");
      setNewChannel("generic");
      setNewSmtpHost("");
      setNewToAddrs("");
      setNewNtfyToken("");
      setNewMqttTopic("");
      setShowCreate(false);
      await load();
    } catch (e) {
      setCreateError((e as Error).message);
    }
  };

  const toggleActive = async (wh: Webhook) => {
    try {
      await apiFetch(`/api/webhooks/${wh.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !wh.is_active }),
      });
      showToast(`Webhook ${wh.id} ${wh.is_active ? "paused" : "resumed"}.`, "success");
      await load();
    } catch (e) {
      showToast(`Failed: ${(e as Error).message}`, "danger");
    }
  };

  const deleteWebhook = async (wh: Webhook) => {
    const confirmed = await showConfirm(
      `Delete webhook ${wh.id}? "${wh.url}" will stop receiving events.`,
      { icon: "trash", iconColor: "text-danger", btnClass: "btn-danger", btnLabel: "Delete" },
    );
    if (!confirmed) return;
    try {
      await apiFetch(`/api/webhooks/${wh.id}`, { method: "DELETE" });
      showToast(`Webhook ${wh.id} deleted.`, "success");
      await load();
    } catch (e) {
      showToast(`Delete failed: ${(e as Error).message}`, "danger");
    }
  };

  const testWebhook = async (wh: Webhook) => {
    try {
      await apiFetch(`/api/webhooks/${wh.id}/test`, { method: "POST" });
      showToast(`Test event fired to webhook ${wh.id}.`, "success");
    } catch (e) {
      showToast(`Test failed: ${(e as Error).message}`, "danger");
    }
  };

  if (loading) return <Spinner message="Loading webhooks..." />;
  if (error) return <ErrorAlert message={error} />;

  const visible = table.view(webhooks, "url", "channel_type");

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">
          <i class="bi bi-broadcast me-2" />
          Webhooks
        </h5>
        <div class="btn-group btn-group-sm">
          <button
            class="btn btn-outline-secondary"
            onClick={() => void load()}
            title="Refresh"
            aria-label="Refresh"
          >
            <i class="bi bi-arrow-clockwise" />
          </button>
          <button class="btn btn-outline-success" onClick={() => setShowCreate(!showCreate)}>
            <i class="bi bi-plus-lg me-1" />
            New Webhook
          </button>
        </div>
      </div>

      {lastCreated && (
        <div class="alert alert-warning border-warning">
          <div class="d-flex justify-content-between align-items-start">
            <div>
              <strong>
                <i class="bi bi-key me-1" />
                Webhook signing secret — copy now
              </strong>
              <div class="small text-muted mt-1">
                Webhook <code>{lastCreated.id}</code> (<span>{lastCreated.url}</span>) uses
                HMAC-SHA256 signatures. The secret is shown <strong>only once</strong>; ShoreGuard
                cannot recover it later.
              </div>
              <pre class="bg-dark text-warning p-2 rounded mt-2 mb-0 sg-fs-sm">
                {lastCreated.secret}
              </pre>
            </div>
            <button
              class="btn btn-sm btn-outline-secondary"
              onClick={() => setLastCreated(null)}
              title="Dismiss"
            >
              <i class="bi bi-x-lg" />
            </button>
          </div>
        </div>
      )}

      {showCreate && (
        <div class="card sg-card-themed mb-3">
          <div class="card-body">
            {createError && <div class="alert alert-danger py-1 small">{createError}</div>}
            <div class="row g-2">
              <div class="col-md-6">
                <label class="form-label small text-muted mb-1">Target URL</label>
                <input
                  type="text"
                  class="form-control form-control-sm"
                  placeholder="https://hooks.slack.com/..."
                  value={newUrl}
                  onInput={(e) => setNewUrl((e.target as HTMLInputElement).value)}
                />
              </div>
              <div class="col-md-3">
                <label class="form-label small text-muted mb-1">Channel</label>
                <ChannelSelect value={newChannel} onChange={setNewChannel} />
              </div>
              <div class="col-md-3 d-flex align-items-end">
                <button class="btn btn-sm btn-success w-100" onClick={() => void createWebhook()}>
                  <i class="bi bi-plus-lg me-1" />
                  Create
                </button>
              </div>
              <div class="col-12">
                <label class="form-label small text-muted mb-1">
                  Event types <span class="text-muted">(comma-separated)</span>
                </label>
                <input
                  type="text"
                  class="form-control form-control-sm"
                  placeholder="sandbox.created, gateway.registered, approval.approved"
                  value={newEvents}
                  onInput={(e) => setNewEvents((e.target as HTMLInputElement).value)}
                />
                <div class="form-text small">
                  Known events: <code>sandbox.created</code>, <code>sandbox.deleted</code>,{" "}
                  <code>gateway.registered</code>, <code>gateway.unregistered</code>,{" "}
                  <code>policy.updated</code>, <code>inference.updated</code>,{" "}
                  <code>approval.approved</code>, <code>approval.rejected</code>,{" "}
                  <code>webhook.test</code>. Use <code>*</code> to subscribe to everything.
                </div>
              </div>
              {newChannel === "email" && (
                <div class="col-12">
                  <div class="row g-2">
                    <div class="col-md-6">
                      <label class="form-label small text-muted mb-1">
                        SMTP host{" "}
                        <span class="text-muted">
                          (optional with <code>SHOREGUARD_SMTP_HOST</code>)
                        </span>
                      </label>
                      <input
                        type="text"
                        class="form-control form-control-sm"
                        placeholder="smtp.example.com:587"
                        value={newSmtpHost}
                        onInput={(e) => setNewSmtpHost((e.target as HTMLInputElement).value)}
                      />
                    </div>
                    <div class="col-md-6">
                      <label class="form-label small text-muted mb-1">
                        To addresses (comma-separated)
                      </label>
                      <input
                        type="text"
                        class="form-control form-control-sm"
                        placeholder="ops@example.com"
                        value={newToAddrs}
                        onInput={(e) => setNewToAddrs((e.target as HTMLInputElement).value)}
                      />
                    </div>
                  </div>
                </div>
              )}
              {newChannel === "ntfy" && (
                <div class="col-12">
                  <div class="row g-2">
                    <div class="col-md-6">
                      <label class="form-label small text-muted mb-1">
                        Access token <span class="text-muted">(optional)</span>
                      </label>
                      <input
                        type="password"
                        class="form-control form-control-sm"
                        placeholder="tk_..."
                        value={newNtfyToken}
                        onInput={(e) => setNewNtfyToken((e.target as HTMLInputElement).value)}
                      />
                    </div>
                    <div class="col-md-6 d-flex align-items-end">
                      <div class="form-text small">
                        Target URL is the topic URL you subscribe to, e.g.{" "}
                        <code>https://ntfy.sh/my-topic</code>. Approvals arrive as high-priority
                        pushes.
                      </div>
                    </div>
                  </div>
                </div>
              )}
              {newChannel === "telegram" && (
                <div class="col-12">
                  <div class="form-text small">
                    Target URL is your bot's sendMessage endpoint including the chat id, e.g.{" "}
                    <code>
                      https://api.telegram.org/bot&lt;TOKEN&gt;/sendMessage?chat_id=&lt;CHAT&gt;
                    </code>
                    . Create a bot via @BotFather; @userinfobot tells you the chat id.
                  </div>
                </div>
              )}
              {newChannel === "mqtt" && (
                <div class="col-12">
                  <div class="row g-2">
                    <div class="col-md-6">
                      <label class="form-label small text-muted mb-1">
                        Base topic <span class="text-muted">(optional)</span>
                      </label>
                      <input
                        type="text"
                        class="form-control form-control-sm"
                        placeholder="shoreguard"
                        value={newMqttTopic}
                        onInput={(e) => setNewMqttTopic((e.target as HTMLInputElement).value)}
                      />
                    </div>
                    <div class="col-md-6 d-flex align-items-end">
                      <div class="form-text small">
                        Target URL is the broker, e.g. <code>mqtt://192.168.1.10:1883</code>.
                        Events publish to <code>&lt;topic&gt;/&lt;event&gt;</code> — ideal for
                        Home Assistant automations.
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {webhooks.length > 0 && (
        <div class="mb-2">
          <input
            type="text"
            class="form-control form-control-sm sg-mw-300"
            placeholder="Filter webhooks..."
            value={table.filterText}
            onInput={(e) => table.setFilterText((e.target as HTMLInputElement).value)}
          />
        </div>
      )}

      {webhooks.length > 0 ? (
        <div class="table-responsive">
          <table class="table table-striped table-sm align-middle">
            <thead>
              <tr>
                <th class={table.sortClass("id")} onClick={() => table.sortBy("id")}>
                  ID
                </th>
                <th
                  class={table.sortClass("channel_type")}
                  onClick={() => table.sortBy("channel_type")}
                >
                  Channel
                </th>
                <th>URL</th>
                <th>Events</th>
                <th>Active</th>
                <th
                  class={`d-none d-md-table-cell ${table.sortClass("created_at")}`}
                  onClick={() => table.sortBy("created_at")}
                >
                  Created
                </th>
                <th class="text-end sg-w-160">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((wh) => (
                <tr key={wh.id}>
                  <td>
                    <strong>{wh.id}</strong>
                  </td>
                  <td>
                    <span class="badge text-bg-info">{wh.channel_type}</span>
                  </td>
                  <td class="font-monospace small text-truncate sg-mw-300" title={wh.url}>
                    {wh.url}
                  </td>
                  <td>
                    {(wh.event_types || []).map((et) => (
                      <span key={et} class="badge text-bg-secondary me-1 sg-fs-sm">
                        {et}
                      </span>
                    ))}
                  </td>
                  <td>
                    <span class={`badge ${wh.is_active ? "text-bg-success" : "text-bg-secondary"}`}>
                      {wh.is_active ? "active" : "paused"}
                    </span>
                  </td>
                  <td class="d-none d-md-table-cell small text-muted">
                    {formatDateTime(wh.created_at)}
                  </td>
                  <td class="text-end">
                    <button
                      class="btn btn-sm text-muted"
                      title="Send test event"
                      onClick={() => void testWebhook(wh)}
                    >
                      <i class="bi bi-send" />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title="Delivery log"
                      onClick={() => setDeliveriesFor(wh)}
                    >
                      <i class="bi bi-list-check" />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title={wh.is_active ? "Pause" : "Resume"}
                      onClick={() => void toggleActive(wh)}
                    >
                      <i class={`bi ${wh.is_active ? "bi-pause-circle" : "bi-play-circle"}`} />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title="Edit"
                      onClick={() => setEditing(wh)}
                    >
                      <i class="bi bi-pencil" />
                    </button>
                    <button
                      class="btn btn-sm text-muted"
                      title="Delete"
                      onClick={() => void deleteWebhook(wh)}
                    >
                      <i class="bi bi-trash3" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState icon="broadcast" message="No webhooks registered.">
          <button class="btn btn-success btn-sm" onClick={() => setShowCreate(true)}>
            <i class="bi bi-plus me-1" />
            Create Webhook
          </button>
        </EmptyState>
      )}

      {editing && (
        <EditWebhookModal
          webhook={editing}
          onSaved={() => {
            setEditing(null);
            void load();
          }}
          onClose={() => setEditing(null)}
        />
      )}
      {deliveriesFor && (
        <DeliveriesModal webhook={deliveriesFor} onClose={() => setDeliveriesFor(null)} />
      )}
    </div>
  );
}
