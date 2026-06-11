/**
 * Typed API surface for islands.
 *
 * `types.gen.ts` is generated from the FastAPI OpenAPI schema by
 * `scripts/generate_api_types.py` — regenerate it after changing REST
 * schemas. Islands can type responses precisely via the `Schema`
 * helper, e.g. `Schema<"GatewayInfo">`, while `apiFetch` stays the
 * single transport.
 */

import type { components } from "./types.gen";

export { apiFetch } from "../lib/api";

/** Resolve a named schema from the generated OpenAPI components. */
export type Schema<T extends keyof components["schemas"]> = components["schemas"][T];
