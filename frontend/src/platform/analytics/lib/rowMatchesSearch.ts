import type { QueryRow } from "../types";

/**
 * Client-side "search anything" matcher over a loaded query row.
 *
 * The /queries API has no free-text search (only exact chat_id lookup), so the
 * QueryFilterBar / table search boxes filter the rows already loaded on the
 * page across the user-visible fields, mirroring the design's behavior.
 */
export function rowMatchesSearch(r: QueryRow, search: string): boolean {
  const q = search.trim().toLowerCase();
  if (!q) return true;
  return (
    r.query.toLowerCase().includes(q) ||
    (r.chat_id ?? "").toLowerCase().includes(q) ||
    (r.session_id ?? "").toLowerCase().includes(q) ||
    r.status.toLowerCase().includes(q) ||
    r.services_used.some((s) => s.toLowerCase().includes(q)) ||
    String(r.metrics.cost_usd).includes(q)
  );
}
