import { KNOWLEDGE_HEALTH_CONTRACT } from "@/generated/knowledgeContract";
import type {
  AnalyticsFilterOptions,
  AnalyticsLimits,
  AnalyticsOption,
} from "@/platform/analytics/constants";
import type {
  AnalyticsWindow,
  NodeDatum,
  QueryLogResponse,
  QueryRow,
  Scorecard,
  ScorecardNode,
  TrendMetric,
  TrendsResponse,
} from "@/platform/analytics/types";

import { APIS } from "./api";

/**
 * The knowledge service's `/health/*` responses → the shapes the agent
 * dashboard's analytics components already consume.
 *
 * The Analytics screen is that page, not a copy of it: its toolbar, tabs, KPI
 * cards, charts and tables are imported from `src/app/dashboard/analytics`, and
 * only the endpoints and these few field differences belong to this product.
 * Reconciling them here is what makes reuse possible without loosening types
 * the main dashboard depends on.
 *
 * What actually differs (checked against a live collection, 2026-08-23):
 *
 * | dashboard field              | knowledge service      | resolved by      |
 * | ---------------------------- | ---------------------- | ---------------- |
 * | `agent_id`                   | `knowledge_id`         | renamed          |
 * | `name`                       | absent                 | null             |
 * | `quality.unanswered`         | `no_answer` + `error`  | summed           |
 * | `quality.failed_api_requests`| `error`                | renamed          |
 * | `quality.budget_exhausted`   | absent                 | 0 (no budget)    |
 * | `trend`                      | present but unused     | omitted (below)  |
 * | `QueryRow.timestamp`         | `created_at`           | renamed          |
 * | `QueryRow.metrics.*`         | flat on the row        | nested           |
 *
 * Nothing here invents a measurement: every filled field is a rename, a sum of
 * two of the service's own counters, or a zero for a quantity this service does
 * not meter. Genuinely unknown values stay `null` so a card renders "—" rather
 * than a confident zero.
 */

const num = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? (value as Record<string, unknown>) : {};

const asArray = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [];

/**
 * The one service family this product owns.
 *
 * Rows already carry `services_used: ["knowledge"]`, so this is only the
 * fallback for a row that carries none.
 */
const KNOWLEDGE_SERVICES = ["knowledge"] as const;

/** `perf.by_node` and `cost.by_node` are one object shape on this service. */
function toScorecardNode(raw: unknown): ScorecardNode {
  const node = asRecord(raw);
  return {
    node_name: String(node.node_name ?? ""),
    model: (node.model as string | null) ?? null,
    provider: (node.provider as string | null) ?? null,
    queries: num(node.queries),
    total_calls: num(node.total_calls),
    errors: num(node.errors),
    avg_latency_ms: num(node.avg_latency_ms),
    p50_latency_ms: num(node.p50_latency_ms),
    p95_latency_ms: num(node.p95_latency_ms),
    avg_latency_s: num(node.avg_latency_s),
    p50_latency_s: num(node.p50_latency_s),
    p95_latency_s: num(node.p95_latency_s),
    total_tokens: num(node.total_tokens),
    input_tokens: num(node.input_tokens),
    output_tokens: num(node.output_tokens),
    total_cost: num(node.total_cost),
  };
}

export function adaptScorecard(raw: unknown): Scorecard {
  const body = asRecord(raw);
  const usage = asRecord(body.usage);
  const quality = asRecord(body.quality);
  const perf = asRecord(body.perf);
  const cost = asRecord(body.cost);
  const feedback = body.quality ? asRecord(quality.feedback) : null;

  const noAnswer = num(quality.no_answer) ?? 0;
  const errored = num(quality.error) ?? 0;

  return {
    agent_id: String(body.knowledge_id ?? ""),
    // The scorecard does not echo the collection's name, and this screen takes
    // its heading from the shell rather than from the payload.
    name: null,
    period: String(body.period ?? ""),
    since: String(body.since ?? ""),
    until: String(body.until ?? ""),
    previous_since: String(body.previous_since ?? ""),
    usage: {
      total_queries: num(usage.total_queries) ?? 0,
      unique_users: num(usage.unique_users) ?? null,
      widget_queries: num(usage.widget_queries) ?? 0,
      per_day: asArray(usage.per_day).map((entry) => {
        const row = asRecord(entry);
        // `day` is the date and `bucket` the ISO timestamp of the same point;
        // the charts key off `day`, and fall back when the bucket is hourly.
        return {
          day: String(row.day ?? row.bucket ?? ""),
          queries: num(row.queries) ?? 0,
        };
      }),
    },
    quality: {
      answered: num(quality.answered) ?? 0,
      // The service splits "not answered" into a question it could not answer
      // from the documents and a request that errored. The dashboard's
      // `unanswered` means both, and `failed_api_requests` means the second.
      unanswered: noAnswer + errored,
      success_rate: num(quality.success_rate) ?? 0,
      failed_api_requests: errored,
      no_answer: noAnswer,
      // This service enforces no per-question token budget, so this is 0
      // because it is true, not because the number is missing.
      budget_exhausted: 0,
      feedback: feedback
        ? {
            positive: num(feedback.positive) ?? 0,
            negative: num(feedback.negative) ?? 0,
            total: num(feedback.total) ?? 0,
          }
        : null,
    },
    perf: {
      avg_ms: num(perf.avg_ms) ?? 0,
      p50_ms: num(perf.p50_ms) ?? 0,
      p95_ms: num(perf.p95_ms) ?? 0,
      by_node: asArray(perf.by_node).map(toScorecardNode),
      slowest: asArray(perf.slowest).map((entry) => {
        const row = asRecord(entry);
        return {
          chat_id: String(row.chat_id ?? ""),
          query: String(row.query ?? ""),
          latency_ms: num(row.latency_ms) ?? 0,
          total_tokens: num(row.total_tokens) ?? 0,
          trace_id: String(row.trace_id ?? ""),
        };
      }),
    },
    cost: {
      total_cost: num(cost.total_cost) ?? 0,
      avg_cost_per_query: num(cost.avg_cost_per_query) ?? 0,
      total_tokens: num(cost.total_tokens) ?? 0,
      input_tokens: num(cost.input_tokens) ?? 0,
      output_tokens: num(cost.output_tokens) ?? 0,
      // Default false, not true: an absent flag must not claim every question
      // is priced, or unpriced spend silently reads as $0.
      cost_complete: cost.cost_complete === true,
      by_model: asArray(cost.by_model).map((entry) => {
        const row = asRecord(entry);
        return {
          provider: String(row.provider ?? ""),
          model: String(row.model ?? ""),
          queries: num(row.queries) ?? 0,
          cost: num(row.cost) ?? 0,
        };
      }),
      by_node: asArray(cost.by_node).map(toScorecardNode),
      most_expensive: asArray(cost.most_expensive).map((entry) => {
        const row = asRecord(entry);
        return {
          chat_id: String(row.chat_id ?? ""),
          query: String(row.query ?? ""),
          total_tokens: num(row.total_tokens) ?? 0,
          cost_usd: num(row.cost_usd) ?? 0,
          trace_id: String(row.trace_id ?? ""),
        };
      }),
    },
    // `trend` is deliberately dropped. The screen fetches a second scorecard for
    // the preceding window and derives every delta from it, which covers more
    // metrics than this block carries and labels them against a window the page
    // knows the bounds of.
  };
}

/** The metric names this page's charts read, as the shared type spells them. */
const TREND_METRIC_NAMES: TrendMetric[] = [
  "success_rate",
  "avg_ms",
  "p95_ms",
  "cost",
  "failures",
  "total_queries",
];

export function adaptTrends(raw: unknown): TrendsResponse {
  const body = asRecord(raw);
  const series = asArray(body.series).map((entry) => {
    const row = asRecord(entry);
    const point: TrendsResponse["series"][number] = {
      bucket: String(row.bucket ?? ""),
      trusted: row.trusted !== false,
      total_queries: num(row.total_queries) ?? 0,
    };
    for (const metric of [
      "success_rate",
      "avg_ms",
      "p95_ms",
      "cost",
      "failures",
    ] as const) {
      const value = num(row[metric]);
      if (value !== undefined) point[metric] = value;
    }
    return point;
  });

  const regression = asRecord(body.regression);
  return {
    agent_id: String(body.knowledge_id ?? ""),
    bucket: (body.bucket as TrendsResponse["bucket"]) ?? "day",
    buckets: num(body.buckets) ?? series.length,
    since: String(body.since ?? series[0]?.bucket ?? ""),
    until: String(body.until ?? series[series.length - 1]?.bucket ?? ""),
    // Only the metrics the charts know how to plot: the route echoes back the
    // list it was asked for, and a name added server-side has no chart yet.
    metrics: asArray(body.metrics)
      .map(String)
      .filter((metric): metric is TrendMetric =>
        TREND_METRIC_NAMES.includes(metric as TrendMetric),
      ),
    series,
    regression: {
      detected: regression.detected === true,
      metric: regression.metric as string | undefined,
      from_bucket: regression.from_bucket as string | undefined,
      to_bucket: regression.to_bucket as string | undefined,
      delta: num(regression.delta),
      note: regression.note as string | undefined,
    },
  };
}

/** Days in each rolling preset, matching what the service means by them. */
const PRESET_DAYS: Record<string, number> = { week: 7, month: 30 };

/** The most buckets `/health/trends` will return in one request. */
const MAX_BUCKETS = KNOWLEDGE_HEALTH_CONTRACT.trends.buckets.max ?? 52;

/**
 * The window parameters `/health/trends` takes.
 *
 * It is the one health route with no `period`: its window is `bucket` length ×
 * `buckets`, so a 7-day view is `bucket=day&buckets=7`. Sending `period`
 * instead would fall through to the route's own `buckets=8` default and plot
 * eight weeks under a toolbar that says seven days.
 *
 * Only the window is set here. `bucket` and `metrics` belong to the shared
 * `useTrends` hook — it asks for the series its own charts plot, and its params
 * are merged over these, so naming them here would be dead weight that reads
 * like a decision.
 */
export function trendsWindowParams(
  window: AnalyticsWindow,
): Record<string, unknown> {
  if (window.mode === "custom") {
    return { start_date: window.start, end_date: window.end };
  }
  return { buckets: Math.min(PRESET_DAYS[window.period] ?? 7, MAX_BUCKETS) };
}

function toNodeDatum(raw: unknown): NodeDatum {
  const node = asRecord(raw);
  return {
    node_name: String(node.node_name ?? ""),
    provider: (node.provider as string | null) ?? null,
    model: (node.model as string | null) ?? null,
    calls: num(node.calls) ?? num(node.total_calls),
    input_tokens: num(node.input_tokens) ?? 0,
    output_tokens: num(node.output_tokens) ?? 0,
    total_tokens: num(node.total_tokens) ?? 0,
    // The service prices a node whole; it does not split spend by direction.
    input_cost: num(node.input_cost) ?? 0,
    output_cost: num(node.output_cost) ?? 0,
    total_cost: num(node.total_cost) ?? 0,
    latency_ms: num(node.latency_ms) ?? num(node.avg_latency_ms),
    total_latency_ms: num(node.total_latency_ms),
  };
}

function toQueryRow(raw: unknown): QueryRow {
  const row = asRecord(raw);
  const nodeData = asArray(row.node_data).map(toNodeDatum);
  const services = asArray(row.services_used).map(String);

  return {
    chat_id: String(row.chat_id ?? ""),
    session_id: (row.session_id as string | null) ?? null,
    user_id: (row.user_id as string | null) ?? null,
    timestamp: String(row.created_at ?? ""),
    query: String(row.query ?? ""),
    refined_query: (row.refined_query as string | null) ?? null,
    route: (row.route as string | null) ?? null,
    services_used: services.length ? services : [...KNOWLEDGE_SERVICES],
    status: String(row.status ?? ""),
    outcome: String(row.outcome ?? ""),
    is_answered: row.is_answered === true,
    stop_reason: (row.stop_reason as string | null) ?? null,
    confidence: (row.confidence as string | null) ?? null,
    failure_category: (row.failure_category as string | null) ?? null,
    failed_stage: (row.failed_stage as string | null) ?? null,
    failure_detail: (row.failure_detail as string | null) ?? null,
    provider: (row.provider as string | null) ?? null,
    model: (row.model as string | null) ?? null,
    from_widget: row.from_widget === true,
    key_type: (row.key_type as string | null) ?? null,
    feedback: (row.feedback as QueryRow["feedback"]) ?? null,
    trace_id: (row.trace_id as string | null) ?? null,
    // Flat on this service's row; the dashboard's components read them nested.
    metrics: {
      latency_ms: num(row.latency_ms) ?? 0,
      input_tokens: num(row.input_tokens) ?? 0,
      output_tokens: num(row.output_tokens) ?? 0,
      total_tokens: num(row.total_tokens) ?? 0,
      cost_usd: num(row.cost_usd) ?? 0,
      cost_complete: row.cost_complete === true,
      retry_count: num(row.retry_count) ?? 0,
      node_data: nodeData.length ? nodeData : null,
      // The service records no per-node latency of our own outside `node_data`,
      // and a synthesized entry would put a number where none was measured.
      service_calls: null,
    },
  };
}

export function adaptQueryLog(raw: unknown): QueryLogResponse {
  const body = asRecord(raw);
  const queries = asArray(body.queries).map(toQueryRow);
  return {
    agent_id: String(body.knowledge_id ?? ""),
    period: String(body.period ?? ""),
    sort: String(body.sort ?? ""),
    order: String(body.order ?? ""),
    total_matching: num(body.total_matching) ?? queries.length,
    limit: num(body.limit) ?? queries.length,
    offset: num(body.offset) ?? 0,
    returned: num(body.returned) ?? queries.length,
    queries,
  };
}

/* ------------------------------------------------------ filter vocabularies */

const INITIALISMS = new Set(["sql", "db", "api", "id", "llm", "usd", "ms"]);

/** `no_answer` → `No answer`, `cost_usd` → `Cost USD`. */
export function humanizeCode(code: string): string {
  return code
    .split("_")
    .filter(Boolean)
    .map((word, index) => {
      if (INITIALISMS.has(word)) return word.toUpperCase();
      // Sentence case, so a label reads as a phrase rather than a headline.
      return index === 0
        ? word.charAt(0).toUpperCase() + word.slice(1)
        : word.toLowerCase();
    })
    .join(" ");
}

type Endpoint = keyof typeof KNOWLEDGE_HEALTH_CONTRACT;

/** The values the spec documents for one parameter, as dropdown options. */
export function contractOptions(
  endpoint: Endpoint,
  param: string,
): AnalyticsOption[] {
  const spec = (
    KNOWLEDGE_HEALTH_CONTRACT[endpoint] as Record<
      string,
      { values: readonly string[] | null } | undefined
    >
  )[param];
  return (spec?.values ?? []).map((value) => ({
    value,
    label: humanizeCode(value),
  }));
}

/** A numeric bound the spec publishes, or the given fallback. */
export function contractNumber(
  endpoint: Endpoint,
  param: string,
  field: "default" | "min" | "max",
  fallback: number,
): number {
  const spec = (
    KNOWLEDGE_HEALTH_CONTRACT[endpoint] as Record<
      string,
      Record<string, unknown> | undefined
    >
  )[param];
  const value = spec?.[field];
  return typeof value === "number" ? value : fallback;
}

/**
 * Rows-per-page choices for a paginated route, from the two numbers the spec
 * publishes — its default page and its hard cap. Asking for more than the cap
 * is a 422, which is the bug this avoids.
 */
export function pageSizeLadder(endpoint: Endpoint, param = "limit"): number[] {
  const base = contractNumber(endpoint, param, "default", 20);
  const max = contractNumber(endpoint, param, "max", base);
  return [...new Set([base, base * 2, max])]
    .filter((size) => size > 0 && size <= max)
    .sort((a, b) => a - b);
}

/**
 * `status` on `/health/queries` — the one vocabulary the service does not
 * publish.
 *
 * Its schema is a bare string with no description, so nothing can be extracted
 * from the spec. These three are the values the route actually filters on,
 * verified against a live collection on 2026-08-23: `status=success` returned
 * exactly the answered questions, `status=no_answer` exactly the unanswered
 * ones, and both match the counters `quality` reports (`answered`, `no_answer`,
 * `error`). Sending a value the route does not know is not an error — it is
 * ignored, and the log comes back unfiltered — so a wrong list here would be
 * invisible.
 */
const QUERY_STATUSES = ["success", "no_answer", "error"] as const;

/**
 * What each filter on the Analytics screen may send.
 *
 * `routes` is deliberately empty: the dashboard's service filter sends
 * `route=`, and this service ignores that parameter (verified live — `route`,
 * `outcome` and `min_cost_usd` all returned the unfiltered log). Publishing
 * options for a filter that cannot filter is worse than publishing none: the
 * reader picks one and the table does not change.
 */
export const KNOWLEDGE_FILTER_OPTIONS: AnalyticsFilterOptions = {
  routes: [],
  routesLabel: "Route",
  // `outcome` is the parameter this service ignores; `status` is the one it
  // filters on, so the vocabulary goes in the `statuses` slot and the Status
  // dropdown sends it under that name.
  outcomes: [],
  statuses: QUERY_STATUSES.map((value) => ({
    value,
    label: value === "success" ? "Answered" : humanizeCode(value),
  })),
  feedback: contractOptions("queries", "feedback").filter(
    // `any` and `rated` both mean "rated either way", which the three explicit
    // choices plus "All" already cover; offering them reads as extra ratings.
    (option) => option.value !== "any" && option.value !== "rated",
  ),
  // `/health/scorecard` takes no `bucket` — `usage.per_day` is always daily —
  // so the toolbar's granularity control has nothing to offer here.
  buckets: [],
  costStrategies: [],
  rowsPerPage: pageSizeLadder("queries"),
};

export const KNOWLEDGE_ANALYTICS_LIMITS: AnalyticsLimits = {
  scorecardTopN: contractNumber("scorecard", "top_n", "default", 10),
  queriesPageSize: contractNumber("queries", "limit", "default", 20),
  failuresPageSize: contractNumber("failures", "limit", "default", 50),
};

/**
 * The endpoint templates the shared analytics hooks fill in.
 *
 * Those hooks address one subject as `{agent_id}` — `buildApiUrl` substitutes
 * that literal name. This product's routes are keyed by `{knowledge_id}`, which
 * is what `APIS` must say for the contract test to check them against the
 * service's own document, so the rename happens here, once, rather than by
 * keeping a second copy of each path.
 */
const asAgentScoped = (template: string) =>
  template.replace("{knowledge_id}", "{agent_id}");

export const KNOWLEDGE_ANALYTICS_APIS = {
  SCORECARD: asAgentScoped(APIS.HEALTH_SCORECARD),
  TRENDS: asAgentScoped(APIS.HEALTH_TRENDS),
  QUERIES: asAgentScoped(APIS.HEALTH_QUERIES),
  SYNC_COSTS: asAgentScoped(APIS.HEALTH_SYNC_COSTS),
} as const;

/**
 * `/health/failures` is not read by any screen today — the artifact's Analytics
 * has no Failures tab, and the counts it would show are already on Quality. The
 * route stays in `APIS` and in the generated contract so the reason it is
 * absent is a design decision on record rather than an oversight, and so
 * bringing it back needs a component, not a rediscovery.
 */

export const KNOWLEDGE_ANALYTICS_ADAPTERS = {
  scorecard: adaptScorecard,
  trends: adaptTrends,
  queries: adaptQueryLog,
} as const;

/** Only `trends` differs; the others take `period` or the date pair. */
export const KNOWLEDGE_WINDOW_PARAMS = { trends: trendsWindowParams } as const;
