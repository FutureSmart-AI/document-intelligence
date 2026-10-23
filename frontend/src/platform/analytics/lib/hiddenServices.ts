import type { QueryLogResponse, QueryRow } from "../types";

/**
 * Does this node/service name belong to a hidden service family?
 *
 * Names are not one-to-one: Knowledge appears as `knowledge` in
 * `services_used`, as the `rag` node in `node_data`, and as the `rag` service
 * in `service_calls` — with prefixed variants (`rag_retrieval`) on some
 * payloads. Matching the family rather than the exact string is what keeps a
 * renamed node from leaking back in.
 */
export function isHiddenService(
  name: string | null | undefined,
  hidden: readonly string[],
): boolean {
  if (!name) return false;
  const n = name.toLowerCase();
  return hidden.some(
    (h) => n === h || n.startsWith(`${h}_`) || n.startsWith(`${h}-`),
  );
}

function stripRow(row: QueryRow, hidden: readonly string[]): QueryRow {
  const { node_data, service_calls } = row.metrics;
  return {
    ...row,
    services_used: (row.services_used ?? []).filter(
      (service) => !isHiddenService(service, hidden),
    ),
    metrics: {
      ...row.metrics,
      node_data:
        node_data?.filter((n) => !isHiddenService(n.node_name, hidden)) ??
        node_data,
      service_calls:
        service_calls?.filter(
          (c) => !isHiddenService(c.node_name ?? c.service, hidden),
        ) ?? service_calls,
    },
  };
}

/**
 * Drop every hidden service's entries from a query-log page.
 *
 * Rows are kept, not filtered: a `multi` query that used both Knowledge and
 * NL2SQL is still an NL2SQL query, and dropping it would disagree with the
 * server's `total_matching` and break pagination. Only the parts of the row
 * that name the hidden service go.
 *
 * Row-level totals (`latency_ms`, `cost_usd`, tokens) are the whole query's and
 * stay whole — they cannot be split per service from this payload.
 */
export function stripHiddenServices(
  res: QueryLogResponse,
  hidden: readonly string[],
): QueryLogResponse {
  if (!hidden.length) return res;
  return { ...res, queries: res.queries.map((row) => stripRow(row, hidden)) };
}
