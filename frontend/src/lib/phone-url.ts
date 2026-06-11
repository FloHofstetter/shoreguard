/**
 * Helpers for the "Open on phone" QR dialog.
 *
 * The QR code must encode an address the *phone* can reach. When the
 * operator browses via localhost, the current location is useless on
 * another device — these helpers detect that and rewrite the URL onto
 * a LAN candidate reported by GET /api/system/access-urls.
 */

const LOOPBACK_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Whether a hostname only resolves on this machine. */
export function isLoopbackHostname(hostname: string): boolean {
  return LOOPBACK_HOSTNAMES.has(hostname) || hostname.endsWith(".localhost");
}

/**
 * Re-host `current` onto `candidate` (an absolute http(s) URL),
 * keeping path, query, and hash so the phone lands on the same view.
 */
export function rewriteToCandidate(current: string, candidate: string): string {
  const cur = new URL(current);
  const next = new URL(candidate);
  next.pathname = cur.pathname;
  next.search = cur.search;
  next.hash = cur.hash;
  return next.toString();
}
