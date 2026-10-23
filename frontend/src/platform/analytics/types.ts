export type AnalyticsPeriod = "week" | "month";
export type AnalyticsWindow =
  | { mode: "preset"; period: AnalyticsPeriod }
  | { mode: "custom"; start: string; end: string }; // YYYY-MM-DD
export interface WindowRequestParams {
  period?: AnalyticsPeriod;
  start_date?: string;
  end_date?: string;
}
export type AnalyticsTabKey =
  "overview" | "quality" | "performance" | "cost" | "querylogs";

// Scorecard (fields used by Overview + shared cards)
export interface ScorecardUsage {
  total_queries: number;
  unique_users: number | null;
  widget_queries: number;
  per_day: { day: string; queries: number }[];
}
export interface ScorecardQuality {
  answered: number;
  unanswered: number;
  success_rate: number;
  failed_api_requests: number;
  no_answer: number;
  budget_exhausted: number;
  feedback: ScorecardFeedback | null;
}
export interface ScorecardFeedback {
  positive: number;
  negative: number;
  total: number;
}
// Per-node aggregate for the window (scorecard perf & cost share the shape).
// Sub-service nodes (nl2sql, integration) may omit provider/model.
export interface ScorecardNode {
  node_name: string;
  model?: string | null;
  provider?: string | null;
  queries?: number;
  total_calls?: number;
  errors?: number;
  avg_latency_ms?: number;
  p50_latency_ms?: number;
  p95_latency_ms?: number;
  avg_latency_s?: number;
  p50_latency_s?: number;
  p95_latency_s?: number;
  total_latency_ms?: number;
  total_tokens?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_cost?: number;
  input_cost?: number;
  output_cost?: number;
}
export interface ScorecardPerf {
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  by_node?: ScorecardNode[];
  slowest: {
    chat_id: string;
    query: string;
    latency_ms: number;
    total_tokens: number;
    trace_id: string;
  }[];
}
export interface ScorecardCost {
  total_cost: number;
  avg_cost_per_query: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  cost_complete: boolean;
  by_model: {
    provider: string;
    model: string;
    queries: number;
    cost: number;
  }[];
  by_node?: ScorecardNode[];
  most_expensive: {
    chat_id: string;
    query: string;
    total_tokens: number;
    cost_usd: number;
    trace_id: string;
  }[];
}
export interface ScorecardTrend {
  prev_success_rate: number;
  success_rate_delta: number | null;
  prev_avg_ms: number;
  avg_ms_delta: number | null;
  prev_total_cost: number;
  cost_delta: number | null;
  prev_failures: number;
  failures_delta: number | null;
  direction: "up" | "down" | "flat";
}
export interface Scorecard {
  agent_id: string;
  name: string | null;
  period: string;
  since: string;
  until: string;
  previous_since: string;
  usage: ScorecardUsage;
  quality: ScorecardQuality;
  perf: ScorecardPerf;
  cost: ScorecardCost;
  // Omitted by the API when there is no prior period to compare against
  // (e.g. a brand-new agent with data only in the current window).
  trend?: ScorecardTrend;
}

// Trends
export type TrendMetric =
  "success_rate" | "avg_ms" | "p95_ms" | "cost" | "failures" | "total_queries";
export interface TrendsResponse {
  agent_id: string;
  bucket: "week" | "day" | "month";
  buckets: number;
  since: string;
  until: string;
  metrics: TrendMetric[];
  series: Array<
    { bucket: string; trusted: boolean; total_queries: number } & Partial<
      Record<TrendMetric, number>
    >
  >;
  regression: {
    detected: boolean;
    metric?: string;
    from_bucket?: string;
    to_bucket?: string;
    delta?: number;
    note?: string;
  };
}

// Query Log
// Per-node LLM breakdown from LangSmith (accurate model + tokens + cost).
// Sub-services (nl2sql / integration) carry tokens + cost but no model/provider.
export interface NodeDatum {
  node_name: string;
  provider: string | null;
  model: string | null;
  // Number of LLM/tool calls the node made for this query.
  calls?: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  input_cost: number;
  output_cost: number;
  total_cost: number;
  // LangSmith run latency — present on some payloads. Used as a fallback for
  // per-node latency when there's no matching `service_calls` entry.
  latency_ms?: number;
  total_latency_ms?: number;
}
// Per-call record measured on our end — latency + tokens per node/service.
export interface ServiceCall {
  node_name?: string;
  service?: string;
  latency_ms?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
}
export interface QueryRowMetrics {
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_complete: boolean;
  retry_count: number;
  node_data?: NodeDatum[] | null;
  service_calls?: ServiceCall[] | null;
}
export interface QueryRow {
  chat_id: string;
  session_id: string | null;
  user_id: string | null;
  timestamp: string;
  query: string;
  refined_query: string | null;
  route: string | null;
  services_used: string[];
  status: string;
  outcome: string;
  is_answered: boolean;
  stop_reason: string | null;
  confidence: string | null;
  failure_category: string | null;
  failed_stage: string | null;
  failure_detail: string | null;
  provider: string | null;
  model: string | null;
  from_widget: boolean;
  key_type: string | null;
  // Documented as "positive" | "negative" | null, but sourced from
  // chat_logs.feedback where booleans / 1 / -1 also occur — normalize with
  // lib/normalizeFeedback before comparing.
  feedback: string | number | boolean | null;
  metrics: QueryRowMetrics;
  trace_id: string | null;
}
export interface QueryLogResponse {
  agent_id: string;
  period: string;
  sort: string;
  order: string;
  total_matching: number;
  limit: number;
  offset: number;
  returned: number;
  queries: QueryRow[];
}
export interface QueryLogFilters {
  status?: string;
  outcome?: string;
  failure_category?: string;
  failed_stage?: string;
  route?: string;
  model?: string;
  min_tokens?: number;
  min_cost_usd?: number;
  min_latency_ms?: number;
  chat_id?: string;
  services_used?: string; // e.g. "rag" | "nl2sql" | "integration"
  feedback?: string; // "pos" | "neg" | "none"
}
