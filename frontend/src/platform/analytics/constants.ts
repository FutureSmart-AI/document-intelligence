import { APIS as KNOWLEDGE_APIS } from "@/data/api";

import type {
  AnalyticsPeriod,
  AnalyticsTabKey,
  AnalyticsWindow,
  WindowRequestParams,
} from "./types";

/**
 * The analytics reads, on the knowledge service.
 *
 * The shared hooks address their subject as `{agent_id}` — `buildApiUrl`
 * substitutes that literal name — while the knowledge routes name it
 * `{knowledge_id}`, so the rename happens here, once. The agent dashboard's
 * `/api/dashboard/v1/agents/…` defaults these used to fall back to are gone:
 * a tab that forgot to override one called the dashboard service.
 */
const asAgentScoped = (template: string) =>
  template.replace("{knowledge_id}", "{agent_id}");

export const APIS = {
  SCORECARD: asAgentScoped(KNOWLEDGE_APIS.HEALTH_SCORECARD),
  TRENDS: asAgentScoped(KNOWLEDGE_APIS.HEALTH_TRENDS),
  QUERIES: asAgentScoped(KNOWLEDGE_APIS.HEALTH_QUERIES),
  SYNC_COSTS: asAgentScoped(KNOWLEDGE_APIS.HEALTH_SYNC_COSTS),
} as const;

/** One choice in a filter dropdown: the value the API is sent, and its label. */
export interface AnalyticsOption {
  value: string;
  label: string;
}

export interface AnalyticsFilterOptions {
  /** `route` on `/queries`. */
  routes: readonly AnalyticsOption[];
  /**
   * What that control is called. The dashboard's routes are service families,
   * NL2SQL's are query plans; "Services Used" would be a lie on one of them.
   */
  routesLabel: string;
  /** `outcome` on `/queries`. */
  outcomes: readonly AnalyticsOption[];
  /**
   * `status` on `/queries`, for a backend that filters on the literal status
   * instead of on the coarse answered/unanswered `outcome`.
   *
   * When this is non-empty the Status dropdown offers these and sends them as
   * `status`; when it is empty the dropdown falls back to `outcomes`. The two
   * are not interchangeable and the difference is invisible without this:
   * the knowledge service accepts `status` and *ignores* `outcome`, so a value
   * sent under the wrong name returns the whole log with no error at all.
   */
  statuses: readonly AnalyticsOption[];
  /** `feedback` on `/queries` — the option value *is* the API value. */
  feedback: readonly AnalyticsOption[];
  /**
   * `bucket` on `/scorecard`. Empty means the backend publishes no granularity
   * parameter, and the control that sets it is not rendered — sending a param
   * a service does not whitelist is a 422.
   */
  buckets: readonly AnalyticsOption[];
  /** `strategy` on `POST /sync-costs`. Empty means the control is not shown. */
  costStrategies: readonly AnalyticsOption[];
  /** Rows-per-page choices, bounded by whatever the backend caps `limit` at. */
  rowsPerPage: readonly number[];
}

export interface AnalyticsLimits {
  /** `top_n` on `/scorecard` — how many slowest / most expensive rows to size. */
  scorecardTopN: number;
  /** Initial rows per page in the query tables. */
  queriesPageSize: number;
  /** `limit` on `/failures`. */
  failuresPageSize: number;
}

/**
 * The agent dashboard's own vocabularies.
 *
 * Written out here rather than generated because the chat service publishes no
 * machine-readable spec for `/api/dashboard/v1/agents/{id}/queries`; these are
 * the values that endpoint accepts. A product whose backend *does* publish one
 * should derive its options from it and override this through
 * `AnalyticsScopeProvider` — see the NL2SQL app, which reads the service's
 * OpenAPI document at codegen time.
 */
export const DEFAULT_FILTER_OPTIONS: AnalyticsFilterOptions = {
  routes: [
    { value: "knowledge", label: "Knowledge (RAG)" },
    { value: "nl2sql", label: "NL2SQL" },
    { value: "integration", label: "Integration" },
    { value: "custom_action", label: "Custom Action" },
    { value: "connected_agent", label: "Connected Agent" },
    { value: "multi", label: "Multi" },
    { value: "direct", label: "Direct" },
  ],
  routesLabel: "Services Used",
  // "Answered"/"Unanswered" rather than "Success"/"Failure": `outcome=success`
  // returns every answered query across all services, whereas the literal
  // `status=success` only matches that one status and drops Knowledge-answered
  // rows. The label names what the filter does, not what the value is called.
  outcomes: [
    { value: "success", label: "Answered" },
    { value: "failure", label: "Unanswered" },
  ],
  // Full words, not "1"/"-1" or "pos"/"neg": the API matches on
  // `positive | negative | none | any`, and a short form silently matches
  // nothing.
  feedback: [
    { value: "positive", label: "👍 Positive" },
    { value: "negative", label: "👎 Negative" },
    { value: "none", label: "No Feedback" },
  ],
  // Empty: this backend filters on `outcome`, above.
  statuses: [],
  buckets: [],
  costStrategies: [],
  rowsPerPage: [10, 25, 50, 100],
};

export const DEFAULT_LIMITS: AnalyticsLimits = {
  scorecardTopN: 10,
  queriesPageSize: 25,
  failuresPageSize: 50,
};

export const ANALYTICS_TABS: { key: AnalyticsTabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "quality", label: "Quality & Accuracy" },
  { key: "performance", label: "Performance" },
  { key: "cost", label: "Cost" },
  { key: "querylogs", label: "Query Logs" },
];

/**
 * What a chart draws at before its container has been measured.
 *
 * recharts defaults this to `{-1, -1}`, which makes every `ResponsiveContainer`
 * log "The width(-1) and height(-1) of chart should be greater than 0" on each
 * render that happens before its resize observer reports — hundreds of lines
 * while this page's data loads. These are the card sizes the charts sit in, so
 * the first frame is drawn at roughly the right size and then corrected.
 */
export const CHART_INITIAL_DIMENSION = { width: 600, height: 240 };

export const DEFAULT_WINDOW: AnalyticsWindow = {
  mode: "preset",
  period: "week",
};
const VALID_PERIODS: AnalyticsPeriod[] = ["week", "month"];
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function windowToRequestParams(w: AnalyticsWindow): WindowRequestParams {
  return w.mode === "preset"
    ? { period: w.period }
    : { start_date: w.start, end_date: w.end };
}

export function parseWindowParams(sp: URLSearchParams): AnalyticsWindow {
  const start = sp.get("start");
  const end = sp.get("end");
  if (start && end && ISO_DATE.test(start) && ISO_DATE.test(end)) {
    return { mode: "custom", start, end };
  }
  const period = sp.get("period");
  if (period && VALID_PERIODS.includes(period as AnalyticsPeriod)) {
    return { mode: "preset", period: period as AnalyticsPeriod };
  }
  return DEFAULT_WINDOW;
}

export function windowKey(w: AnalyticsWindow): string {
  return w.mode === "preset" ? `p:${w.period}` : `c:${w.start}:${w.end}`;
}
