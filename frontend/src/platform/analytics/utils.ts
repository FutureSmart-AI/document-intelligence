import type { AnalyticsWindow, Scorecard } from "./types";

export function getHttpStatus(err: unknown): number | undefined {
  return (err as { response?: { status?: number } } | null | undefined)
    ?.response?.status;
}

/**
 * Previous-period window = the equal-length span immediately before the current
 * window, derived from the scorecard's resolved `since`/`until` bounds.
 *
 * Fetching a second scorecard for it is how every card gets a "vs prev period"
 * delta, including the metrics the API returns no `trend.*_delta` for — and it
 * works against any backend that accepts `start_date`/`end_date`, which is why
 * the NL2SQL product gets the same pills for free.
 */
export function buildPreviousWindow(sc?: Scorecard): AnalyticsWindow | null {
  if (!sc?.since || !sc?.until) return null;
  const since = new Date(`${sc.since.slice(0, 10)}T00:00:00Z`);
  const until = new Date(`${sc.until.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(since.getTime()) || Number.isNaN(until.getTime())) {
    return null;
  }
  const durationMs = until.getTime() - since.getTime();
  const prevEnd = new Date(since.getTime() - 86_400_000); // day before `since`
  const prevStart = new Date(prevEnd.getTime() - durationMs);
  return {
    mode: "custom",
    start: prevStart.toISOString().slice(0, 10),
    end: prevEnd.toISOString().slice(0, 10),
  };
}

// Zero-filled scorecard so the dashboard renders with all metrics at 0 when an
// agent has no analytics yet, instead of a bare "no data" message.
export function emptyScorecard(
  agentId: string,
  window: AnalyticsWindow,
): Scorecard {
  const period = window.mode === "preset" ? window.period : "custom";
  return {
    agent_id: agentId,
    name: null,
    period,
    since: "",
    until: "",
    previous_since: "",
    usage: {
      total_queries: 0,
      unique_users: 0,
      widget_queries: 0,
      per_day: [],
    },
    quality: {
      answered: 0,
      unanswered: 0,
      success_rate: 0,
      failed_api_requests: 0,
      no_answer: 0,
      budget_exhausted: 0,
      feedback: { positive: 0, negative: 0, total: 0 },
    },
    perf: {
      avg_ms: 0,
      p50_ms: 0,
      p95_ms: 0,
      slowest: [],
    },
    cost: {
      total_cost: 0,
      avg_cost_per_query: 0,
      total_tokens: 0,
      input_tokens: 0,
      output_tokens: 0,
      cost_complete: true,
      by_model: [],
      most_expensive: [],
    },
    trend: {
      prev_success_rate: 0,
      success_rate_delta: null,
      prev_avg_ms: 0,
      avg_ms_delta: null,
      prev_total_cost: 0,
      cost_delta: null,
      prev_failures: 0,
      failures_delta: null,
      direction: "flat",
    },
  };
}
