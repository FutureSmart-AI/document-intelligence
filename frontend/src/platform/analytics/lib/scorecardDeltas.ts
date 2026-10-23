import type { Scorecard } from "../types";
import { pctChange, type PctChange } from "./pctChange";

/**
 * Per-metric % change vs the previous period, for the KPI cards the scorecard
 * API doesn't return a `trend.*_delta` for (queries, users, avg cost, tokens,
 * p50/p95). Derived client-side by diffing the current-window scorecard against
 * a second scorecard fetched for the previous window, so every top card can
 * show a "vs prev period" pill like the design.
 */
export interface ScorecardDeltas {
  total_queries: PctChange | null;
  unique_users: PctChange | null;
  success_rate: PctChange | null;
  avg_ms: PctChange | null;
  avg_cost_per_query: PctChange | null;
  total_cost: PctChange | null;
  total_tokens: PctChange | null;
  p50_ms: PctChange | null;
  p95_ms: PctChange | null;
}

const delta = (cur?: number | null, prev?: number | null): PctChange | null => {
  if (cur == null || prev == null) return null;
  return pctChange(cur - prev, prev);
};

export function computeScorecardDeltas(
  cur?: Scorecard,
  prev?: Scorecard,
): ScorecardDeltas | undefined {
  if (!cur || !prev) return undefined;
  return {
    total_queries: delta(cur.usage?.total_queries, prev.usage?.total_queries),
    unique_users: delta(cur.usage?.unique_users, prev.usage?.unique_users),
    success_rate: delta(cur.quality?.success_rate, prev.quality?.success_rate),
    avg_ms: delta(cur.perf?.avg_ms, prev.perf?.avg_ms),
    total_cost: delta(cur.cost?.total_cost, prev.cost?.total_cost),
    avg_cost_per_query: delta(
      cur.cost?.avg_cost_per_query,
      prev.cost?.avg_cost_per_query,
    ),
    total_tokens: delta(cur.cost?.total_tokens, prev.cost?.total_tokens),
    p50_ms: delta(cur.perf?.p50_ms, prev.perf?.p50_ms),
    p95_ms: delta(cur.perf?.p95_ms, prev.perf?.p95_ms),
  };
}
