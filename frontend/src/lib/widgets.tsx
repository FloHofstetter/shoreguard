/** Small shared presentational components (port of components.js). */

import type { ComponentChildren } from "preact";

import { badgeClass, GATEWAY_TYPE_ICONS } from "./constants";

export function Spinner({ message = "Loading..." }: { message?: string }) {
  return (
    <div class="text-center text-muted py-5">
      <div class="spinner-border spinner-border-sm me-2" />
      {message}
    </div>
  );
}

export function ErrorAlert({ message }: { message: string }) {
  return <div class="alert alert-danger">{message}</div>;
}

export function EmptyState({
  icon,
  message,
  children,
}: {
  icon: string;
  message: string;
  children?: ComponentChildren;
}) {
  return (
    <div class="text-center text-muted py-5">
      <i class={`bi bi-${icon} fs-1 d-block mb-3`} />
      <p>{message}</p>
      {children}
    </div>
  );
}

export function StatusBadge({
  status,
  group,
}: {
  status: string;
  group: "phase" | "approval" | "gateway" | "role";
}) {
  return <span class={`badge ${badgeClass(group, status)}`}>{status}</span>;
}

export function GatewayTypeIcon({ type }: { type: string }) {
  const info = GATEWAY_TYPE_ICONS[type];
  if (!info) return <span>{type}</span>;
  return (
    <span>
      <i class={`bi bi-${info.icon} me-1`} />
      {info.label}
    </span>
  );
}

const GATEWAY_STATUS_ICONS: Record<string, string> = {
  connected: "circle-fill",
  running: "circle-fill",
  unreachable: "exclamation-circle",
  stopped: "stop-circle",
  offline: "circle",
};

const GATEWAY_STATUS_LABELS: Record<string, string> = {
  connected: "Connected",
  running: "Running",
  unreachable: "Unreachable",
  stopped: "Stopped",
  offline: "Offline",
};

export function GatewayStatusBadge({ gw }: { gw: { status?: string; version?: string } }) {
  const status = gw.status || "offline";
  const icon = GATEWAY_STATUS_ICONS[status] ?? "circle";
  const label = GATEWAY_STATUS_LABELS[status] ?? status;
  return (
    <span>
      <span class={`badge ${badgeClass("gateway", status)}`}>
        <i class={`bi bi-${icon} me-1`} />
        {label}
      </span>
      {status === "connected" && gw.version && (
        <span class="text-muted small ms-1">{gw.version}</span>
      )}
    </span>
  );
}

export function Card({
  title,
  icon,
  children,
}: {
  title: string;
  icon: string;
  children: ComponentChildren;
}) {
  return (
    <div class="card sg-card-themed">
      <div class="card-body">
        <h6 class="text-muted mb-3">
          <i class={`bi bi-${icon} me-2`} />
          {title}
        </h6>
        {children}
      </div>
    </div>
  );
}

export function EndpointBadges({
  endpoints,
  max = 3,
}: {
  endpoints: { host: string; port: number }[] | undefined;
  max?: number;
}) {
  if (!endpoints || endpoints.length === 0) return <span class="text-muted">—</span>;
  const display = endpoints.slice(0, max);
  const moreCount = endpoints.length - max;
  return (
    <span>
      {display.map((ep) => (
        <span key={`${ep.host}:${ep.port}`} class="badge endpoint-badge me-1">
          {ep.host}:{ep.port}
        </span>
      ))}
      {moreCount > 0 && <span class="badge text-bg-secondary">+{moreCount}</span>}
    </span>
  );
}
