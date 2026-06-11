/** Network rule detail & editor (island), incl. GraphQL L7 matchers. */

import { useEffect, useRef, useState } from "preact/hooks";

import { apiFetch } from "../lib/api";
import { ensureAuth, hasRole } from "../lib/auth";
import { API, gwUrl, navigateTo } from "../lib/constants";
import { showConfirm, showToast } from "../lib/notify";
import { ErrorAlert, Spinner } from "../lib/widgets";

interface QueryMatcher {
  param: string;
  type: "glob" | "any";
  value: string;
}

interface L7RuleEdit {
  id: number;
  method: string;
  path: string;
  command: string;
  operation_type: string;
  operation_name: string;
  fields: string;
  query: QueryMatcher[];
}

interface EndpointEdit {
  id: number;
  host: string;
  port: number;
  protocol: string;
  tls: string;
  enforcement: string;
  access: string;
  allow_encoded_slash: boolean;
  persisted_queries: string;
  graphql_max_body_bytes: number;
  path: string;
  rules: L7RuleEdit[];
}

interface WireQueryMatcher {
  glob?: string;
  any?: string[];
}

interface WireL7Allow {
  method?: string;
  path?: string;
  command?: string;
  operation_type?: string;
  operation_name?: string;
  fields?: string[];
  query?: Record<string, WireQueryMatcher>;
}

interface WireEndpoint {
  host: string;
  port?: number;
  protocol?: string;
  tls?: string;
  enforcement?: string;
  access?: string;
  allow_encoded_slash?: boolean;
  persisted_queries?: string;
  graphql_max_body_bytes?: number;
  path?: string;
  rules?: { allow?: WireL7Allow }[];
}

interface WireRule {
  name?: string;
  endpoints?: WireEndpoint[];
  binaries?: { path: string }[];
}

export default function RuleDetailPage({ name, ruleKey }: { name: string; ruleKey: string }) {
  const isNew = ruleKey === "_new";
  const [loading, setLoading] = useState(!isNew);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(isNew);
  const [rule, setRule] = useState<WireRule | null>(null);
  const [isOperator, setIsOperator] = useState(false);

  const [editKey, setEditKey] = useState(isNew ? "" : ruleKey);
  const [editName, setEditName] = useState("");
  const [editBinaries, setEditBinaries] = useState("");
  const [endpoints, setEndpoints] = useState<EndpointEdit[]>([]);
  const epCounter = useRef(0);
  const l7Counter = useRef(0);

  const emptyEndpoint = (): EndpointEdit => ({
    id: epCounter.current++,
    host: "",
    port: 443,
    protocol: "",
    tls: "",
    enforcement: "",
    access: "",
    allow_encoded_slash: false,
    persisted_queries: "",
    graphql_max_body_bytes: 0,
    path: "",
    rules: [],
  });

  const mapL7Rule = (r: { allow?: WireL7Allow }): L7RuleEdit => {
    const allow = r.allow ?? {};
    const query: QueryMatcher[] = Object.entries(allow.query ?? {}).map(([param, matcher]) => ({
      param,
      type: matcher.glob ? "glob" : "any",
      value: matcher.glob || (matcher.any ?? []).join(", "),
    }));
    return {
      id: l7Counter.current++,
      method: allow.method ?? "",
      path: allow.path ?? "",
      command: allow.command ?? "",
      operation_type: allow.operation_type ?? "",
      operation_name: allow.operation_name ?? "",
      fields: (allow.fields ?? []).join(", "),
      query,
    };
  };

  useEffect(() => {
    void ensureAuth().then(() => setIsOperator(hasRole("operator")));
    if (isNew) {
      setEndpoints([emptyEndpoint()]);
      return;
    }
    void (async () => {
      setLoading(true);
      setError("");
      try {
        const data = await apiFetch<{ policy?: { network_policies?: Record<string, WireRule> } }>(
          `${API}/sandboxes/${name}/policy`,
        );
        const found = data.policy?.network_policies?.[ruleKey];
        if (!found) {
          setError(`Rule "${ruleKey}" not found.`);
          return;
        }
        setRule(found);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, ruleKey]);

  const switchToEdit = () => {
    if (!rule) return;
    setEditKey(ruleKey);
    setEditName(rule.name ?? "");
    setEditBinaries((rule.binaries ?? []).map((b) => b.path).join("\n"));
    setEndpoints(
      (rule.endpoints ?? []).map((ep) => ({
        id: epCounter.current++,
        host: ep.host ?? "",
        port: ep.port ?? 443,
        protocol: ep.protocol ?? "",
        tls: ep.tls ?? "",
        enforcement: ep.enforcement ?? "",
        access: ep.access ?? "",
        allow_encoded_slash: Boolean(ep.allow_encoded_slash),
        persisted_queries: ep.persisted_queries ?? "",
        graphql_max_body_bytes: ep.graphql_max_body_bytes ?? 0,
        path: ep.path ?? "",
        rules: (ep.rules ?? []).map(mapL7Rule),
      })),
    );
    setEditing(true);
  };

  const cancelEdit = () => {
    if (isNew) {
      navigateTo(gwUrl(`/sandboxes/${name}/policy`));
    } else {
      setEditing(false);
    }
  };

  const updateEndpoint = (id: number, patch: Partial<EndpointEdit>) => {
    setEndpoints((prev) => prev.map((ep) => (ep.id === id ? { ...ep, ...patch } : ep)));
  };

  const updateL7Rule = (epId: number, ruleId: number, patch: Partial<L7RuleEdit>) => {
    setEndpoints((prev) =>
      prev.map((ep) =>
        ep.id === epId
          ? { ...ep, rules: ep.rules.map((r) => (r.id === ruleId ? { ...r, ...patch } : r)) }
          : ep,
      ),
    );
  };

  const save = async () => {
    const key = isNew ? editKey.trim() : ruleKey;
    const eps = endpoints
      .filter((ep) => ep.host.trim())
      .map((ep) => {
        const o: WireEndpoint = { host: ep.host.trim(), port: Number(ep.port) || 443 };
        if (ep.protocol) o.protocol = ep.protocol;
        if (ep.tls) o.tls = ep.tls;
        if (ep.enforcement) o.enforcement = ep.enforcement;
        if (ep.access) o.access = ep.access;
        if (ep.allow_encoded_slash) o.allow_encoded_slash = true;
        if (ep.protocol === "graphql") {
          if (ep.persisted_queries) o.persisted_queries = ep.persisted_queries;
          if (ep.graphql_max_body_bytes) o.graphql_max_body_bytes = ep.graphql_max_body_bytes;
          if (ep.path) o.path = ep.path;
        }
        const rules = ep.rules
          .filter(
            (r) =>
              r.method || r.path || r.command || r.operation_type || r.operation_name || r.fields,
          )
          .map((r) => {
            const allow: WireL7Allow = {};
            if (r.method) allow.method = r.method;
            if (r.path) allow.path = r.path;
            if (r.command) allow.command = r.command;
            if (r.operation_type) allow.operation_type = r.operation_type;
            if (r.operation_name) allow.operation_name = r.operation_name;
            if (r.fields) {
              const fields = r.fields
                .split(",")
                .map((f) => f.trim())
                .filter(Boolean);
              if (fields.length) allow.fields = fields;
            }
            const query: Record<string, WireQueryMatcher> = {};
            for (const qm of r.query.filter((q) => q.param.trim())) {
              if (qm.type === "glob") {
                query[qm.param.trim()] = { glob: qm.value.trim() };
              } else {
                query[qm.param.trim()] = {
                  any: qm.value
                    .split(",")
                    .map((v) => v.trim())
                    .filter(Boolean),
                };
              }
            }
            if (Object.keys(query).length) allow.query = query;
            return { allow };
          });
        if (rules.length) o.rules = rules;
        return o;
      });
    const binaries = editBinaries.trim()
      ? editBinaries
          .split("\n")
          .map((p) => ({ path: p.trim() }))
          .filter((b) => b.path)
      : [];

    if (!key) {
      showToast("Rule key is required.", "warning");
      return;
    }
    if (eps.length === 0) {
      showToast("At least one endpoint is required.", "warning");
      return;
    }

    try {
      await apiFetch(`${API}/sandboxes/${name}/policy/network-rules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, rule: { name: editName.trim() || key, endpoints: eps, binaries } }),
      });
      showToast(`Rule "${key}" saved.`, "success");
      navigateTo(gwUrl(`/sandboxes/${name}/rules/${key}`));
    } catch (e) {
      showToast(`Failed to save: ${(e as Error).message}`, "danger");
    }
  };

  const deleteRule = async () => {
    const confirmed = await showConfirm(`Delete network rule "${ruleKey}"?`, {
      icon: "trash",
      iconColor: "text-danger",
      btnClass: "btn-danger",
      btnLabel: "Delete",
    });
    if (!confirmed) return;
    try {
      await apiFetch(`${API}/sandboxes/${name}/policy/network-rules/${ruleKey}`, {
        method: "DELETE",
      });
      showToast(`Rule "${ruleKey}" deleted.`, "success");
      navigateTo(gwUrl(`/sandboxes/${name}/policy`));
    } catch (e) {
      showToast(`Failed to delete: ${(e as Error).message}`, "danger");
    }
  };

  if (loading) return <Spinner message="Loading rule..." />;
  if (error) return <ErrorAlert message={error} />;

  if (!editing && rule) {
    return (
      <div>
        <div class="d-flex justify-content-between align-items-center mb-4">
          <div>
            <h4 class="mb-1">{rule.name || ruleKey}</h4>
            <span class="text-muted small">
              Key: <code>{ruleKey}</code>
            </span>
          </div>
          {isOperator && (
            <div class="d-flex gap-2">
              <button class="btn btn-outline-primary btn-sm" onClick={switchToEdit}>
                <i class="bi bi-pencil me-1" />
                Edit
              </button>
              <button class="btn btn-outline-danger btn-sm" onClick={() => void deleteRule()}>
                <i class="bi bi-trash me-1" />
                Delete
              </button>
            </div>
          )}
        </div>

        <h6 class="text-muted mb-2">
          <i class="bi bi-globe me-1" />
          Endpoints
          <span class="badge text-bg-secondary ms-1">{(rule.endpoints ?? []).length}</span>
        </h6>
        {(rule.endpoints ?? []).length > 0 ? (
          <div class="table-responsive mb-4">
            <table class="table table-striped table-sm align-middle mb-0">
              <thead>
                <tr>
                  <th>Host</th>
                  <th>Port</th>
                  <th>Protocol</th>
                  <th>TLS</th>
                  <th>Enforcement</th>
                  <th>Access</th>
                  <th>L7 Rules</th>
                </tr>
              </thead>
              <tbody>
                {(rule.endpoints ?? []).map((ep) => (
                  <tr key={`${ep.host}:${ep.port}`}>
                    <td class="font-monospace">{ep.host}</td>
                    <td>{ep.port || 443}</td>
                    <td>
                      {ep.protocol ? (
                        <span class="badge text-bg-secondary">{ep.protocol}</span>
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                    <td>
                      {ep.tls ? (
                        <span class="badge text-bg-info">{ep.tls}</span>
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                    <td>
                      {ep.enforcement ? (
                        <span class="badge text-bg-warning">{ep.enforcement}</span>
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                    <td>
                      {ep.access ? (
                        <span class="badge text-bg-success">{ep.access}</span>
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                    <td>
                      {(ep.rules ?? []).length > 0 ? (
                        <div>
                          {(ep.rules ?? []).map((r, ri) => (
                            <div key={ri} class="mb-1">
                              <span class="badge endpoint-badge me-1">
                                {(r.allow?.method || "*") + " " + (r.allow?.path || "/*")}
                              </span>
                              {r.allow?.command && (
                                <code class="small ms-1">{r.allow.command}</code>
                              )}
                              {r.allow?.query && Object.keys(r.allow.query).length > 0 && (
                                <span class="ms-1">
                                  {Object.entries(r.allow.query).map(([param, matcher]) => (
                                    <span key={param} class="badge text-bg-light border small me-1">
                                      {param}:
                                      {matcher.glob
                                        ? `glob(${matcher.glob})`
                                        : `any(${(matcher.any ?? []).join(",")})`}
                                    </span>
                                  ))}
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <span class="text-muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p class="text-muted mb-4">No endpoints configured.</p>
        )}

        <h6 class="text-muted mb-2">
          <i class="bi bi-terminal me-1" />
          Allowed Binaries
          <span class="badge text-bg-secondary ms-1">{(rule.binaries ?? []).length}</span>
        </h6>
        {(rule.binaries ?? []).length > 0 ? (
          <div class="table-responsive">
            <table class="table table-striped table-sm align-middle mb-0">
              <thead>
                <tr>
                  <th>Path</th>
                </tr>
              </thead>
              <tbody>
                {(rule.binaries ?? []).map((b) => (
                  <tr key={b.path}>
                    <td class="font-monospace small">{b.path}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p class="text-muted">No binary restrictions.</p>
        )}
      </div>
    );
  }

  return (
    <div>
      <div class="d-flex justify-content-between align-items-center mb-4">
        <h4 class="mb-0">{isNew ? "New Network Rule" : `Edit: ${rule?.name || ruleKey}`}</h4>
        {!isNew && (
          <button class="btn btn-outline-secondary btn-sm" onClick={cancelEdit}>
            <i class="bi bi-x me-1" />
            Cancel
          </button>
        )}
      </div>

      <div class="row g-3 mb-4">
        <div class="col-md-6">
          <label class="form-label">Rule Key</label>
          <input
            type="text"
            class="form-control form-control-sm"
            placeholder="e.g. allow-pypi"
            readOnly={!isNew}
            value={editKey}
            onInput={(e) => setEditKey((e.target as HTMLInputElement).value)}
          />
          <div class="form-text">Unique identifier for this rule.</div>
        </div>
        <div class="col-md-6">
          <label class="form-label">Display Name</label>
          <input
            type="text"
            class="form-control form-control-sm"
            placeholder="e.g. PyPI Package Registry"
            value={editName}
            onInput={(e) => setEditName((e.target as HTMLInputElement).value)}
          />
        </div>
      </div>

      <div class="d-flex justify-content-between align-items-center mb-2">
        <h6 class="mb-0">
          <i class="bi bi-globe me-1" />
          Endpoints
        </h6>
        <button
          class="btn btn-outline-success btn-sm"
          type="button"
          onClick={() => setEndpoints([...endpoints, emptyEndpoint()])}
        >
          <i class="bi bi-plus me-1" />
          Add Endpoint
        </button>
      </div>
      <div class="mb-4">
        {endpoints.map((ep) => (
          <div key={ep.id} class="card sg-overlay-card mb-2">
            <div class="card-body py-2 px-3">
              <div class="d-flex justify-content-end mb-2">
                <button
                  class="btn btn-outline-danger btn-sm"
                  type="button"
                  title="Remove endpoint"
                  onClick={() => setEndpoints(endpoints.filter((e2) => e2.id !== ep.id))}
                >
                  <i class="bi bi-x" />
                </button>
              </div>
              <div class="row g-2 mb-2">
                <div class="col-md-6">
                  <label class="form-label small text-muted mb-1">Host</label>
                  <input
                    type="text"
                    class="form-control form-control-sm"
                    placeholder="api.example.com"
                    value={ep.host}
                    onInput={(e) => updateEndpoint(ep.id, { host: (e.target as HTMLInputElement).value })}
                  />
                </div>
                <div class="col-md-2">
                  <label class="form-label small text-muted mb-1">Port</label>
                  <input
                    type="number"
                    class="form-control form-control-sm"
                    placeholder="443"
                    value={ep.port}
                    onInput={(e) =>
                      updateEndpoint(ep.id, {
                        port: parseInt((e.target as HTMLInputElement).value, 10) || 443,
                      })
                    }
                  />
                </div>
                <div class="col-md-4">
                  <label class="form-label small text-muted mb-1">Protocol</label>
                  <select
                    class="form-select form-select-sm"
                    value={ep.protocol}
                    onChange={(e) =>
                      updateEndpoint(ep.id, { protocol: (e.target as HTMLSelectElement).value })
                    }
                  >
                    <option value="">—</option>
                    <option value="rest">REST</option>
                    <option value="graphql">GraphQL</option>
                    <option value="sql">SQL</option>
                  </select>
                </div>
              </div>
              <div class="row g-2">
                <div class="col-md-4">
                  <label class="form-label small text-muted mb-1">TLS</label>
                  <select
                    class="form-select form-select-sm"
                    value={ep.tls}
                    onChange={(e) => updateEndpoint(ep.id, { tls: (e.target as HTMLSelectElement).value })}
                  >
                    <option value="">—</option>
                    <option value="terminate">Terminate</option>
                    <option value="passthrough">Passthrough</option>
                  </select>
                </div>
                <div class="col-md-4">
                  <label class="form-label small text-muted mb-1">Enforcement</label>
                  <select
                    class="form-select form-select-sm"
                    value={ep.enforcement}
                    onChange={(e) =>
                      updateEndpoint(ep.id, { enforcement: (e.target as HTMLSelectElement).value })
                    }
                  >
                    <option value="">—</option>
                    <option value="enforce">Enforce</option>
                    <option value="audit">Audit</option>
                  </select>
                </div>
                <div class="col-md-4">
                  <label class="form-label small text-muted mb-1">Access</label>
                  <select
                    class="form-select form-select-sm"
                    value={ep.access}
                    onChange={(e) =>
                      updateEndpoint(ep.id, { access: (e.target as HTMLSelectElement).value })
                    }
                  >
                    <option value="">—</option>
                    <option value="full">Full</option>
                  </select>
                </div>
              </div>
              <div class="row g-2 mt-1">
                <div class="col-md-12">
                  <div class="form-check">
                    <input
                      class="form-check-input"
                      type="checkbox"
                      id={`allow-encoded-slash-${ep.id}`}
                      checked={ep.allow_encoded_slash}
                      onChange={(e) =>
                        updateEndpoint(ep.id, {
                          allow_encoded_slash: (e.target as HTMLInputElement).checked,
                        })
                      }
                    />
                    <label class="form-check-label small" for={`allow-encoded-slash-${ep.id}`}>
                      Allow percent-encoded slashes (<code>%2F</code>) in paths{" "}
                      <span
                        class="text-muted ms-1"
                        title="Required for upstreams like GitLab that embed %2F in namespaced resource paths. Default: strict — %2F is rejected by the L7 canonicalizer."
                      >
                        <i class="bi bi-question-circle" />
                      </span>
                    </label>
                  </div>
                </div>
              </div>
              {ep.protocol === "graphql" && (
                <div class="row g-2 mt-1">
                  <div class="col-md-4">
                    <label class="form-label small text-muted mb-1">Persisted queries</label>
                    <select
                      class="form-select form-select-sm"
                      value={ep.persisted_queries}
                      onChange={(e) =>
                        updateEndpoint(ep.id, {
                          persisted_queries: (e.target as HTMLSelectElement).value,
                        })
                      }
                    >
                      <option value="">deny (default)</option>
                      <option value="allow_registered">allow registered</option>
                    </select>
                  </div>
                  <div class="col-md-4">
                    <label class="form-label small text-muted mb-1">Max body bytes</label>
                    <input
                      type="number"
                      min={0}
                      class="form-control form-control-sm"
                      placeholder="65536"
                      value={ep.graphql_max_body_bytes}
                      onInput={(e) =>
                        updateEndpoint(ep.id, {
                          graphql_max_body_bytes:
                            parseInt((e.target as HTMLInputElement).value, 10) || 0,
                        })
                      }
                    />
                  </div>
                  <div class="col-md-4">
                    <label class="form-label small text-muted mb-1">Path glob</label>
                    <input
                      type="text"
                      class="form-control form-control-sm"
                      placeholder="/graphql"
                      value={ep.path}
                      onInput={(e) =>
                        updateEndpoint(ep.id, { path: (e.target as HTMLInputElement).value })
                      }
                    />
                  </div>
                </div>
              )}
              <div class="mt-2 border-top pt-2">
                <div class="d-flex justify-content-between align-items-center mb-1">
                  <span class="small text-muted fw-semibold">L7 Rules</span>
                  <button
                    class="btn btn-outline-success btn-sm py-0 px-1"
                    type="button"
                    onClick={() =>
                      updateEndpoint(ep.id, {
                        rules: [
                          ...ep.rules,
                          {
                            id: l7Counter.current++,
                            method: "",
                            path: "",
                            command: "",
                            operation_type: "",
                            operation_name: "",
                            fields: "",
                            query: [],
                          },
                        ],
                      })
                    }
                  >
                    <i class="bi bi-plus" /> Rule
                  </button>
                </div>
                {ep.rules.map((l7) => (
                  <div key={l7.id} class="card bg-body-tertiary mb-1">
                    <div class="card-body py-1 px-2">
                      <div class="d-flex justify-content-end">
                        <button
                          class="btn btn-outline-danger btn-sm py-0 px-1"
                          type="button"
                          title="Remove rule"
                          onClick={() =>
                            updateEndpoint(ep.id, {
                              rules: ep.rules.filter((r) => r.id !== l7.id),
                            })
                          }
                        >
                          <i class="bi bi-x" />
                        </button>
                      </div>
                      <div class="row g-2 mb-1">
                        <div class="col-md-3">
                          <label class="form-label small text-muted mb-0">Method</label>
                          <select
                            class="form-select form-select-sm"
                            value={l7.method}
                            onChange={(e) =>
                              updateL7Rule(ep.id, l7.id, {
                                method: (e.target as HTMLSelectElement).value,
                              })
                            }
                          >
                            <option value="">* (any)</option>
                            {["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"].map(
                              (m) => (
                                <option key={m} value={m}>
                                  {m}
                                </option>
                              ),
                            )}
                          </select>
                        </div>
                        <div class="col-md-5">
                          <label class="form-label small text-muted mb-0">Path</label>
                          <input
                            type="text"
                            class="form-control form-control-sm"
                            placeholder="/api/v1/*"
                            value={l7.path}
                            onInput={(e) =>
                              updateL7Rule(ep.id, l7.id, {
                                path: (e.target as HTMLInputElement).value,
                              })
                            }
                          />
                        </div>
                        <div class="col-md-4">
                          <label class="form-label small text-muted mb-0">Command</label>
                          <input
                            type="text"
                            class="form-control form-control-sm"
                            placeholder="e.g. SELECT"
                            value={l7.command}
                            onInput={(e) =>
                              updateL7Rule(ep.id, l7.id, {
                                command: (e.target as HTMLInputElement).value,
                              })
                            }
                          />
                        </div>
                      </div>
                      {ep.protocol === "graphql" && (
                        <div class="row g-2 mb-1">
                          <div class="col-md-3">
                            <label class="form-label small text-muted mb-0">Op type</label>
                            <select
                              class="form-select form-select-sm"
                              value={l7.operation_type}
                              onChange={(e) =>
                                updateL7Rule(ep.id, l7.id, {
                                  operation_type: (e.target as HTMLSelectElement).value,
                                })
                              }
                            >
                              <option value="">* (any)</option>
                              <option value="query">query</option>
                              <option value="mutation">mutation</option>
                              <option value="subscription">subscription</option>
                            </select>
                          </div>
                          <div class="col-md-4">
                            <label class="form-label small text-muted mb-0">Op name (glob)</label>
                            <input
                              type="text"
                              class="form-control form-control-sm"
                              placeholder="GetUser*"
                              value={l7.operation_name}
                              onInput={(e) =>
                                updateL7Rule(ep.id, l7.id, {
                                  operation_name: (e.target as HTMLInputElement).value,
                                })
                              }
                            />
                          </div>
                          <div class="col-md-5">
                            <label class="form-label small text-muted mb-0">
                              Fields (comma-separated globs)
                            </label>
                            <input
                              type="text"
                              class="form-control form-control-sm"
                              placeholder="user.email, user.name"
                              value={l7.fields}
                              onInput={(e) =>
                                updateL7Rule(ep.id, l7.id, {
                                  fields: (e.target as HTMLInputElement).value,
                                })
                              }
                            />
                          </div>
                        </div>
                      )}
                      <div class="mt-1">
                        <div class="d-flex justify-content-between align-items-center mb-1">
                          <span class="small text-muted">Query Matchers</span>
                          <button
                            class="btn btn-outline-secondary btn-sm py-0 px-1"
                            type="button"
                            onClick={() =>
                              updateL7Rule(ep.id, l7.id, {
                                query: [...l7.query, { param: "", type: "glob", value: "" }],
                              })
                            }
                          >
                            <i class="bi bi-plus" /> Matcher
                          </button>
                        </div>
                        {l7.query.map((qm, qi) => (
                          <div key={qi} class="row g-1 mb-1 align-items-end">
                            <div class="col-3">
                              <input
                                type="text"
                                class="form-control form-control-sm"
                                placeholder="param"
                                value={qm.param}
                                onInput={(e) =>
                                  updateL7Rule(ep.id, l7.id, {
                                    query: l7.query.map((q, i) =>
                                      i === qi
                                        ? { ...q, param: (e.target as HTMLInputElement).value }
                                        : q,
                                    ),
                                  })
                                }
                              />
                            </div>
                            <div class="col-3">
                              <select
                                class="form-select form-select-sm"
                                value={qm.type}
                                onChange={(e) =>
                                  updateL7Rule(ep.id, l7.id, {
                                    query: l7.query.map((q, i) =>
                                      i === qi
                                        ? {
                                            ...q,
                                            type: (e.target as HTMLSelectElement).value as
                                              | "glob"
                                              | "any",
                                          }
                                        : q,
                                    ),
                                  })
                                }
                              >
                                <option value="glob">glob</option>
                                <option value="any">any</option>
                              </select>
                            </div>
                            <div class="col-5">
                              <input
                                type="text"
                                class="form-control form-control-sm"
                                placeholder={qm.type === "glob" ? "sk-*" : "json,xml"}
                                value={qm.value}
                                onInput={(e) =>
                                  updateL7Rule(ep.id, l7.id, {
                                    query: l7.query.map((q, i) =>
                                      i === qi
                                        ? { ...q, value: (e.target as HTMLInputElement).value }
                                        : q,
                                    ),
                                  })
                                }
                              />
                            </div>
                            <div class="col-1">
                              <button
                                class="btn btn-outline-danger btn-sm py-0 px-1"
                                type="button"
                                onClick={() =>
                                  updateL7Rule(ep.id, l7.id, {
                                    query: l7.query.filter((_, i) => i !== qi),
                                  })
                                }
                              >
                                <i class="bi bi-x" />
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div class="mb-4">
        <h6>
          <i class="bi bi-terminal me-1" />
          Allowed Binaries
        </h6>
        <textarea
          class="form-control form-control-sm font-monospace"
          rows={4}
          placeholder={"One path per line, e.g.\n/usr/bin/curl\n/usr/bin/git"}
          value={editBinaries}
          onInput={(e) => setEditBinaries((e.target as HTMLTextAreaElement).value)}
        />
        <div class="form-text">One binary path per line.</div>
      </div>

      <div class="d-flex gap-2">
        <button class="btn btn-success" onClick={() => void save()}>
          <i class="bi bi-check me-1" />
          Save Rule
        </button>
        <button class="btn btn-outline-secondary" onClick={cancelEdit}>
          Cancel
        </button>
      </div>
    </div>
  );
}
