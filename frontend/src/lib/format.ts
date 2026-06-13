/** Shared display helpers for islands. */

import { badgeClass } from "./constants";

/** Role → badge class. Single source of truth lives in constants.BADGES.role. */
export function roleBadge(role: string): string {
  return badgeClass("role", role);
}

export function formatDate(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleDateString() : "—";
}

export function formatTimeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diffSec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.floor(diffHr / 24)}d ago`;
}

export function daysUntil(iso: string): number {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
}
