import type { QueryRow } from "../types";

export interface NodeStat {
  node: string;
  avg_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  calls: number;
  /** Distinct sample queries that exercised this node. */
  queries: number;
  /** queries ÷ sample size (0–100). */
  freq_pct: number;
  answered: number;
  unanswered: number;
  /** answered ÷ queries (0–100); null when the node has no queries. */
  answer_rate: number | null;
  errors: number;
  cost: number;
  /** cost ÷ queries; null when the node has no queries. */
  avg_cost: number | null;
  cost_pct: number; // share of total node cost across the sample (0–100)
  /** Avg input/output tokens per node_data entry; null without LLM data. */
  avg_in: number | null;
  avg_out: number | null;
}

/** Nearest-rank percentile over an unsorted list of numbers. */
function percentile(sorted: number[], p: number): number | null {
  if (!sorted.length) return null;
  const rank = Math.ceil((p / 100) * sorted.length);
  return sorted[Math.min(sorted.length - 1, Math.max(0, rank - 1))];
}

/**
 * Aggregate a per-node latency + quality + cost breakdown from a sample of
 * query rows.
 *
 * Latency (avg/p50/p95) and call counts come from each row's
 * `metrics.service_calls` (our-end timing); cost and token averages come from
 * `metrics.node_data` (LangSmith). Both are keyed by node name and unioned.
 * When a node has no `service_calls` entry, its call + latency fall back to
 * `node_data` (LangSmith run latency), so the breakdown still populates on
 * payloads that carry only node_data.
 * Answered/unanswered split per node counts the sample rows that exercised
 * the node, bucketed by the row's `is_answered`. This is a client-side
 * approximation over the passed sample — there is no per-node rollup endpoint.
 *
 * `errors` attributes a query's `failed_stage` to the matching node when that
 * node was exercised by the query (best-effort: some stage names don't line up
 * 1:1 with node names, so those stay at 0 rather than being fabricated).
 */
export function deriveNodeStats(rows: QueryRow[]): NodeStat[] {
  const latencies = new Map<string, number[]>();
  const calls = new Map<string, number>();
  const queries = new Map<string, number>();
  const answered = new Map<string, number>();
  const cost = new Map<string, number>();
  const inTok = new Map<string, number[]>();
  const outTok = new Map<string, number[]>();
  const errors = new Map<string, number>();

  const bump = (m: Map<string, number>, k: string, by = 1) =>
    m.set(k, (m.get(k) ?? 0) + by);
  const push = (m: Map<string, number[]>, k: string, v: number) => {
    const list = m.get(k) ?? [];
    list.push(v);
    m.set(k, list);
  };

  for (const row of rows) {
    const m = row.metrics;
    const touched = new Set<string>();
    // Nodes that had an our-end service_calls record this row — authoritative
    // for latency (wins over node_data run latency).
    const fromServiceCalls = new Set<string>();

    for (const c of m.service_calls ?? []) {
      const key = c.node_name ?? c.service;
      if (!key) continue;
      touched.add(key);
      fromServiceCalls.add(key);
      if (typeof c.latency_ms === "number") push(latencies, key, c.latency_ms);
    }

    const fromNodeData = new Set<string>();
    for (const n of m.node_data ?? []) {
      touched.add(n.node_name);
      fromNodeData.add(n.node_name);
      bump(cost, n.node_name, n.total_cost ?? 0);
      if (typeof n.input_tokens === "number")
        push(inTok, n.node_name, n.input_tokens);
      if (typeof n.output_tokens === "number")
        push(outTok, n.node_name, n.output_tokens);

      // node_data carries LangSmith's authoritative per-node call count; use it
      // (default 1 — the entry means the node ran at least once).
      bump(calls, n.node_name, typeof n.calls === "number" ? n.calls : 1);

      // Latency fallback when our-end service_calls didn't cover this node.
      if (!fromServiceCalls.has(n.node_name)) {
        const nodeLat = n.latency_ms ?? n.total_latency_ms;
        if (typeof nodeLat === "number") push(latencies, n.node_name, nodeLat);
      }
    }

    // Nodes seen only via service_calls (no node_data) — count each call.
    for (const c of m.service_calls ?? []) {
      const key = c.node_name ?? c.service;
      if (key && !fromNodeData.has(key)) bump(calls, key);
    }

    for (const key of touched) {
      bump(queries, key);
      if (row.is_answered) bump(answered, key);
    }

    // Attribute a failed query to the node it failed at, when that node is
    // present in this row's call/cost set.
    if (row.failed_stage && touched.has(row.failed_stage)) {
      bump(errors, row.failed_stage);
    }
  }

  const names = new Set<string>([
    ...calls.keys(),
    ...cost.keys(),
    ...errors.keys(),
  ]);
  const totalCost = [...cost.values()].reduce((a, b) => a + b, 0);
  const sampleSize = rows.length;

  const avgOf = (list?: number[]) =>
    list && list.length ? list.reduce((a, b) => a + b, 0) / list.length : null;

  const stats: NodeStat[] = [...names].map((node) => {
    const lat = (latencies.get(node) ?? []).slice().sort((a, b) => a - b);
    const c = cost.get(node) ?? 0;
    const q = queries.get(node) ?? 0;
    const ans = answered.get(node) ?? 0;
    return {
      node,
      avg_ms: avgOf(lat),
      p50_ms: percentile(lat, 50),
      p95_ms: percentile(lat, 95),
      calls: calls.get(node) ?? 0,
      queries: q,
      freq_pct: sampleSize > 0 ? (q / sampleSize) * 100 : 0,
      answered: ans,
      unanswered: Math.max(0, q - ans),
      answer_rate: q > 0 ? (ans / q) * 100 : null,
      errors: errors.get(node) ?? 0,
      cost: c,
      avg_cost: q > 0 ? c / q : null,
      cost_pct: totalCost > 0 ? (c / totalCost) * 100 : 0,
      avg_in: avgOf(inTok.get(node)),
      avg_out: avgOf(outTok.get(node)),
    };
  });

  // Slowest (by avg) first; nodes without latency sink to the bottom.
  return stats.sort((a, b) => (b.avg_ms ?? -1) - (a.avg_ms ?? -1));
}
