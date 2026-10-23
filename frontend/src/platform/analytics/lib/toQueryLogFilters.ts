import type { QueryFilterValue } from "../components/QueryFilterBar";
import type { QueryLogFilters } from "../types";

/**
 * Map a QueryFilterBar value to the API's QueryLogFilters (search is
 * client-side).
 *
 * No vocabulary translation happens here any more. The bar's options come from
 * `AnalyticsScope.filterOptions`, which publishes the values the backend
 * matches on, so a selected option is already the value to send — the old
 * short-form map ("pos" → "positive") existed only because the options were
 * written next to the dropdown instead.
 */
export function toQueryLogFilters(v: QueryFilterValue): QueryLogFilters {
  const f: QueryLogFilters = {};
  if (v.status) f.status = v.status;
  // Binary answered/unanswered → API `outcome` (success | failure). Captures
  // all unanswered queries (no_answer + error + …), unlike a single status.
  if (v.outcome) f.outcome = v.outcome;
  // The service selector holds a `route` value — the API filters by route, not
  // by services_used (no such param exists on /queries).
  if (v.services_used) f.route = v.services_used;
  if (v.feedback) f.feedback = v.feedback;
  if (v.min_cost_usd != null) f.min_cost_usd = v.min_cost_usd;
  if (v.min_latency_ms != null) f.min_latency_ms = v.min_latency_ms;
  // `search` is intentionally NOT sent: the API has no free-text search (only
  // exact chat_id). Search is applied client-side over loaded rows via
  // rowMatchesSearch — sending it as chat_id would zero out every result.
  return f;
}
