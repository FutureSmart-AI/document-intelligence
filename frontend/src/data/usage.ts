"use client";

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";
import { useOwnerId } from "./session";

/**
 * What the account has spent, and what it is allowed to spend.
 *
 * Every route here is scoped to the owner, not to a collection — the service
 * publishes this whole surface under `/v1/owners/{owner_id}/usage/…`, and says
 * so plainly ("for this account across every knowledge base it has"). So the
 * headline numbers are one request, and the per-collection breakdown is the
 * same request with `group_by: ["knowledge_id"]` rather than one request per
 * collection added up here.
 *
 * Four of them are used:
 *
 * - `limits/status` — this calendar month's counters against the account's
 *   platform limits. `null` anywhere means unlimited.
 * - `limits/screen` — the same allowances, pre-computed into the Limits tab's
 *   rows (used, limit, percent, lifted, enforced, and the wording for each).
 *   The tab reads this one; nothing on it is re-derived here.
 * - `analytics/query` — the Usage screen itself: filters, grouping, time
 *   buckets and a real cost breakdown, over any window.
 * - `analytics/service-options` — the catalogue the filter dropdowns list.
 *
 * The service also publishes `usage/summary`, which groups the same events by
 * service and operation. It is not used: `analytics/query` answers that with
 * `group_by: ["operation"]` and adds the cost breakdown, so naming both would
 * be two ways to ask one question.
 */

/* ------------------------------------------------------------------ limits */

/**
 * `null` in any limit means unlimited on this account.
 *
 * Every field the service's `LimitsInfo` publishes, so a cap it starts sending
 * is a cap this screen already knows the name of.
 */
export interface UsageLimits {
  max_agents: number | null;
  max_indexed_characters: number | null;
  max_knowledge_items: number | null;
  max_conversations_per_month: number | null;
  max_messages_per_month: number | null;
  max_llm_tokens_per_month: number | null;
  /** Sub-caps of `max_knowledge_items`: whichever fills first refuses. */
  max_documents: number | null;
  max_urls: number | null;
  /** A tighter window on the activity `max_messages_per_month` gates. */
  max_messages_per_day: number | null;
  /** Reporting only — this one never refuses a request. */
  max_cost_usd_per_month: number | null;
}

/** `CurrentUsageInfo` — the counters those caps govern. */
export interface CurrentUsage {
  indexed_characters: number;
  knowledge_items: number;
  agents: number;
  documents: number;
  urls: number;
  conversations_this_month: number;
  messages_this_month: number;
  llm_tokens_this_month: number;
  messages_today: number;
  cost_usd_this_month: number;
}

/** `RemainingQuotaInfo` — headroom, `null` where the cap is unlimited. */
export interface RemainingQuota {
  indexed_characters: number | null;
  knowledge_items: number | null;
  agents: number | null;
  conversations: number | null;
  messages: number | null;
  llm_tokens: number | null;
  documents: number | null;
  urls: number | null;
  messages_today: number | null;
  cost_usd: number | null;
}

export interface UsageStatus {
  owner_id: string;
  period: { start: string; end: string };
  limits: UsageLimits;
  current_usage: CurrentUsage;
  remaining: RemainingQuota;
}

/** `max_llm_tokens_per_month` → `LLM tokens`. */
function humanise(field: string): string {
  const words = field.split("_");
  return words
    .map((word, index) =>
      word === "llm"
        ? "LLM"
        : index === 0
          ? word.charAt(0).toUpperCase() + word.slice(1)
          : word,
    )
    .join(" ");
}

export interface QuotaRow {
  key: string;
  label: string;
  used: number;
  limit: number | null;
}

/**
 * The limits strip, built from the answer rather than from a list kept here.
 *
 * Each cap the service returns is paired with the counter it governs by name —
 * `max_indexed_characters` with `indexed_characters`, `max_messages_per_month`
 * with `messages_this_month` — so a cap the service adds appears here without a
 * release, and one it drops stops being drawn. A cap with no matching counter
 * is left out: a bar reading "0 of 10" when nothing was counted is a claim the
 * response did not make.
 */
export function quotaRows(status: UsageStatus | undefined): QuotaRow[] {
  if (!status?.limits) return [];
  const used = (status.current_usage ?? {}) as Record<string, number>;
  return Object.entries(status.limits)
    .map(([field, limit]) => {
      const stripped = field.replace(/^max_/, "");
      // `max_messages_per_day` counts `messages_today`, not `messages` — a
      // daily cap read against the monthly counter would report a figure
      // several times its own window.
      const daily = stripped.endsWith("_per_day");
      const base = stripped.replace(/_per_month$/, "").replace(/_per_day$/, "");
      const counter = (
        daily ? [`${base}_today`] : [base, `${base}_this_month`]
      ).find((name) => name in used);
      if (!counter) return null;
      return {
        key: field,
        label: daily ? `${humanise(base)} today` : humanise(base),
        used: used[counter] ?? 0,
        limit: (limit ?? null) as number | null,
      };
    })
    .filter((row): row is QuotaRow => row !== null);
}

/**
 * This month's usage across the whole account, with the limits beside it.
 *
 * One request — the board's card and the Usage screen both read this hook, so
 * they agree and share a cache.
 */
export function useUsageStatus(): UseQueryResult<UsageStatus> {
  const ownerId = useOwnerId();
  return useQuery<UsageStatus>({
    queryKey: ["di-usage-status", ownerId ?? null],
    enabled: Boolean(ownerId),
    refetchOnWindowFocus: false,
    staleTime: 60 * 1000,
    queryFn: async () => {
      const response = await axios.get<UsageStatus>(
        buildApiUrl(APIS.USAGE_LIMITS, { owner_id: ownerId as string }),
      );
      return response.data;
    },
  });
}

/* ------------------------------------------------------------ limits/screen */

/**
 * One allowance, already resolved into the row the Limits tab draws.
 *
 * Everything the tab used to work out for itself is decided by the service and
 * read here rather than recomputed, which is the whole point of the route:
 *
 * · `percent: null` — draw no bar. Either the ceiling is `lifted` (the
 *   account's own key covers this row, so there is nothing to meter against)
 *   or no counter exists for it yet. The row does not distinguish the two and
 *   the screen does not need it to.
 * · `limit: null` — no ceiling at all. Say "Unlimited"; never a 0% bar, which
 *   reads as "you are out" when nothing is capped. New capped rows come back
 *   this way on accounts created before their defaults were backfilled, so
 *   this is the ordinary path, not an edge case to special-case.
 * · `enforced` — whether going over actually refuses anything. Most rows only
 *   report, and which rows enforce can change without a deploy, so the wording
 *   is read fresh from each answer rather than kept in a list here.
 * · `window` — `current` (a standing count of what is held right now),
 *   `calendar_month`, `calendar_day` or `lifetime`. Read per row: not every
 *   allowance resets on the 1st.
 */
export interface LimitItem {
  key: string;
  label: string;
  used: number;
  limit?: number | null;
  unit: string;
  /** The ceiling in the service's own words, e.g. "10 collections on Trial". */
  maximum: string;
  /** What the row counts, in a sentence. */
  note: string;
  window: string;
  lifted?: boolean;
  percent?: number | null;
  enforced?: boolean;
}

export interface LimitGroup {
  group: string;
  items: LimitItem[];
}

export interface LimitsScreen {
  owner_id: string;
  tier: { name: string; tier_type: string; is_default: boolean };
  window: { start: string; end: string };
  /** Providers this account holds its own key for. */
  own_credentials: string[];
  groups: LimitGroup[];
  /** Keys of the rows with no headroom left. */
  exceeded: string[];
}

/**
 * The whole Limits tab, in one read.
 *
 * Preferred over `useUsageStatus` for that tab: the same allowances, but with
 * the percentages, the wording and the lifted/enforced judgements already made
 * by the service that enforces them.
 */
export function useLimitsScreen(options: { enabled?: boolean } = {}) {
  const ownerId = useOwnerId();
  return useQuery<LimitsScreen>({
    queryKey: ["di-usage-limits-screen", ownerId ?? null],
    enabled: Boolean(ownerId) && options.enabled !== false,
    refetchOnWindowFocus: false,
    staleTime: 60 * 1000,
    queryFn: async () => {
      const response = await axios.get<LimitsScreen>(
        buildApiUrl(APIS.USAGE_LIMITS_SCREEN, { owner_id: ownerId as string }),
      );
      return response.data;
    },
  });
}

/* --------------------------------------------------------------- analytics */

/** Every metric the analytics route totals, all of them optional but count. */
export interface AggregatedMetrics {
  request_count: number;
  total_tokens?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  audio_seconds?: number | null;
  llama_parse_page_count?: number | null;
  processed_weblink_count?: number | null;
  cost?: CostBreakdown | null;
}

export interface CostBreakdown {
  total_cost_usd: number;
  llm_cost_usd?: number | null;
  llm_input_tokens_cost_usd?: number | null;
  llm_output_tokens_cost_usd?: number | null;
  embedding_cost_usd?: number | null;
  document_processing_cost_usd?: number | null;
  transcription_cost_usd?: number | null;
}

/**
 * The account-wide counters the service keeps outside the event stream —
 * documents, characters and pages, rather than requests.
 */
export interface KnowledgeOverviewMetrics {
  total_files_processed: number;
  total_char_count: number;
  total_audio_seconds: number;
  total_llama_parse_pages: number;
  total_weblink_pages: number;
  total_text_count: number;
  total_embedding_tokens: number;
  total_vectors_indexed: number;
  cost?: CostBreakdown | null;
}

export interface AnalyticsDataPoint {
  /** Whichever `group_by` dimensions were asked for, plus the time bucket. */
  dimensions: Record<string, string>;
  metrics: AggregatedMetrics;
}

export interface AnalyticsResponse {
  owner_id: string;
  executed_at: string;
  date_range: DateRange;
  group_by: string[];
  time_bucket?: string | null;
  totals: AggregatedMetrics;
  knowledge_overview_metrics?: KnowledgeOverviewMetrics | null;
  data: AnalyticsDataPoint[];
  total_groups: number;
  has_more: boolean;
}

export interface DateRange {
  start: string;
  end: string;
}

export type TimeBucket = "hour" | "day" | "week" | "month";
/** Whose key the work was billed to — the account's own, or the platform's. */
export type KeyFilter = "owner-key" | "platform-key";

/** The three readings the Source control offers, in the artifact's words. */
export type UsageSource = "total" | "platform" | "owner";

export const USAGE_SOURCES: ReadonlyArray<{ id: UsageSource; label: string }> =
  [
    { id: "total", label: "Total Consumptions" },
    { id: "platform", label: "Platform Consumptions" },
    { id: "owner", label: "Owner Consumptions" },
  ];

export const DEFAULT_SOURCE: UsageSource = "total";

/**
 * What `filters.key_type` must say for a source — and nothing for Total.
 *
 * Total is its own unfiltered call, never platform plus owner. Some events —
 * embeddings, vector writes — carry no key type at all, so they answer to
 * neither filter and the sum undercounts: 267 platform + 2 owner against 336
 * total requests, on a live account on 2026-08-27. Costs happened to add up
 * that day, which is exactly what makes deriving Total dangerous: it looks
 * right until the figure it is wrong about is the one being read.
 */
export function keyTypeFor(source: UsageSource): KeyFilter | undefined {
  if (source === "platform") return "platform-key";
  if (source === "owner") return "owner-key";
  return undefined;
}

/**
 * The filter block a query carries: the reader's selections with the Source
 * control's `key_type` folded in, or nothing at all when neither applies.
 *
 * `undefined` rather than `{}` matters — it keeps an unfiltered query
 * byte-identical to the one the other tabs send, so React Query serves them
 * from one cache entry instead of asking the same question twice.
 */
export function queryFilters(
  selections: AnalyticsFilters | undefined,
  source: UsageSource,
): AnalyticsFilters | undefined {
  const keyType = keyTypeFor(source);
  const value: AnalyticsFilters = { ...selections };
  if (keyType) value.key_type = keyType;
  return Object.keys(value).length ? value : undefined;
}

export interface AnalyticsFilters {
  service_types?: string[];
  operations?: string[];
  labels?: string[];
  /**
   * One collection, and a single id rather than a list — the service takes
   * `knowledge_id` as a string here, unlike the arrays above.
   *
   * Answered only since 2026-08-26; before that both this and
   * `group_by: ["knowledge_id"]` were a 422, which is why the Collections tab
   * and this picker were held back.
   */
  knowledge_id?: string;
  /**
   * Whose key the work was billed to.
   *
   * It belongs *inside* `filters`, beside the others — not as a `key_filter`
   * sibling of `date_range`, which is where this screen used to send it. The
   * route does not reject an unknown top-level field, so the wrong placement
   * came back 200 with every event in the window: the Source control appeared
   * to work and quietly changed nothing.
   */
  key_type?: KeyFilter;
}

export interface AnalyticsQuery {
  date_range: DateRange;
  group_by?: string[];
  time_bucket?: TimeBucket;
  filters?: AnalyticsFilters;
}

/**
 * One analytics query.
 *
 * `enabled` is exposed because the screen runs several of these at once — the
 * current window, the one before it for the deltas, the per-collection
 * grouping — and the tabs that do not need one should not fire it.
 */
export function useAnalytics(
  query: AnalyticsQuery | null,
  options: { enabled?: boolean } = {},
): UseQueryResult<AnalyticsResponse> {
  const ownerId = useOwnerId();
  const enabled =
    Boolean(ownerId) && Boolean(query) && options.enabled !== false;
  return useQuery<AnalyticsResponse>({
    queryKey: ["di-usage-analytics", ownerId ?? null, query],
    enabled,
    refetchOnWindowFocus: false,
    staleTime: 60 * 1000,
    queryFn: async () => {
      const response = await axios.post<AnalyticsResponse>(
        buildApiUrl(APIS.USAGE_ANALYTICS, { owner_id: ownerId as string }),
        query,
      );
      return response.data;
    },
  });
}

export interface LabelOption {
  label: string;
  display_name: string;
}

/**
 * An operation as the catalogue returns it — an object, not a string.
 *
 * Worth stating plainly because the two are mixed in one response:
 * `ServiceTypeOption.operations` is a list of these, while the top-level
 * `all_operations` beside it really is a list of strings. Typing the first as
 * the second renders `[object Object]` into the picker and files a filter value
 * the service will never match.
 */
export interface OperationOption {
  operation: string;
  display_name: string;
  labels?: LabelOption[];
}

export interface ServiceTypeOption {
  service_type: string;
  display_name: string;
  description?: string | null;
  operations?: OperationOption[];
}

export interface ServiceOptions {
  service_types: ServiceTypeOption[];
  /** Flat, and genuinely strings — unlike the per-service lists above. */
  all_operations?: string[];
  all_labels?: string[];
}

/** One entry in a filter picker: what to send, and what to show. */
export interface FilterChoice {
  value: string;
  label: string;
}

const byValue = (choices: FilterChoice[]): FilterChoice[] => {
  const seen = new Map<string, FilterChoice>();
  for (const choice of choices) {
    if (choice.value && !seen.has(choice.value)) seen.set(choice.value, choice);
  }
  return [...seen.values()];
};

/**
 * The service every event on this product is recorded under.
 *
 * `service_type` is a real dimension on the route, but it has exactly one value
 * for a Document Intelligence account, so it is not offered as a filter and the
 * donut does not group by it — a picker with one option, and a ring with one
 * slice, both claim a breakdown that does not exist. `operation` is the
 * dimension that actually varies here.
 */
export const KNOWLEDGE_SERVICE_TYPE = "knowledge";

const knowledgeOperations = (
  options: ServiceOptions | undefined,
): OperationOption[] =>
  (options?.service_types ?? []).find(
    (entry) => entry.service_type === KNOWLEDGE_SERVICE_TYPE,
  )?.operations ?? [];

/**
 * The operations a filter can offer, in the catalogue's own words.
 *
 * `service-options` is the label mapping: it names the five this service
 * records — `data_ingestion`, `llm_call`, `embedding_generation`,
 * `vector_db_write`, `llm_generation` — and carries a `display_name` for each,
 * so nothing here humanises a code the service has already spelled out.
 *
 * The account's own grouped usage is merged in behind them. The catalogue is
 * global, so an operation this account has never run still lists; the grouped
 * answer lists exactly the ones that will return rows, and keeps the picker
 * usable when the catalogue cannot be read at all.
 */
export function operationChoices(
  options: ServiceOptions | undefined,
  observed: AnalyticsResponse | undefined,
): FilterChoice[] {
  return byValue([
    ...knowledgeOperations(options).map((entry) => ({
      value: entry.operation,
      label: entry.display_name || entry.operation,
    })),
    ...(observed?.data ?? [])
      .map((point) => point.dimensions.operation)
      .filter(Boolean)
      .map((name) => ({ value: name, label: name })),
  ]);
}

/**
 * The functional labels a filter can offer, narrowed to one operation when one
 * is chosen.
 *
 * Labels are nested under the operations rather than listed beside them, and
 * they belong to their operation: `generate_answer` only ever appears under
 * `llm_generation`. Offering the whole flat list against a chosen operation
 * would let the reader file a pair that never co-occurs, which reads on screen
 * as "no usage in this period" rather than as an impossible filter.
 *
 * The list is the labels nested under this service's own operations, not the
 * catalogue's flat `all_labels`: that field is platform-wide, so it carries
 * other products' labels — `chart_generation` is NL2SQL's — and a Document
 * Intelligence account filing one gets an empty window rather than a hit. It is
 * read only as a fallback, when the catalogue names no knowledge operation at
 * all and an empty picker would be the alternative.
 *
 * Unlike the operations beside it, this picker has no fallback to the account's
 * own usage, because nothing on this screen reports a label: the grouped answer
 * it would read is `group_by: ["operation"]`, and adding a second grouping to
 * populate a picker would cost a request per window. If the catalogue cannot be
 * read the picker is empty, which is the truth — the names are only there.
 */
export function labelChoices(
  options: ServiceOptions | undefined,
  operation?: string,
): FilterChoice[] {
  const all = knowledgeOperations(options);
  const operations = all.filter(
    (entry) => !operation || entry.operation === operation,
  );

  const nested = operations.flatMap((entry) =>
    (entry.labels ?? []).map((label) => ({
      value: label.label,
      label: label.display_name || label.label,
    })),
  );

  // The flat list stands in only for a catalogue that nests nothing, and never
  // against a chosen operation: it carries no operation, so it cannot be
  // checked against one.
  const nestsNothing = all.every((entry) => !entry.labels?.length);
  const flat =
    operation || !nestsNothing
      ? []
      : (options?.all_labels ?? []).map((name) => ({
          value: name,
          label: name,
        }));

  return byValue([...nested, ...flat]);
}

/**
 * A service type's display name, in the catalogue's own words.
 *
 * Every event this product records carries `knowledge`, so the column this
 * feeds reads the same value down its length today. It is still the service's
 * own word for what did the work, and the day a Document Intelligence account
 * records anything else the column says so without a change here.
 */
export function serviceLabeller(
  options: ServiceOptions | undefined,
): (serviceType: string | undefined) => string {
  const names = new Map(
    (options?.service_types ?? []).map((entry) => [
      entry.service_type,
      entry.display_name || entry.service_type,
    ]),
  );
  return (serviceType) =>
    serviceType ? (names.get(serviceType) ?? serviceType) : "—";
}

/** An operation's display name, for a figure the reader has to read. */
export function operationLabeller(
  options: ServiceOptions | undefined,
): (operation: string | undefined) => string {
  const names = new Map(
    knowledgeOperations(options).map((entry) => [
      entry.operation,
      entry.display_name || entry.operation,
    ]),
  );
  return (operation) => (operation ? (names.get(operation) ?? operation) : "—");
}

/** The service/operation catalogue behind the Custom filters tab. */
export function useServiceOptions(): UseQueryResult<ServiceOptions> {
  const ownerId = useOwnerId();
  return useQuery<ServiceOptions>({
    queryKey: ["di-usage-service-options", ownerId ?? null],
    enabled: Boolean(ownerId),
    refetchOnWindowFocus: false,
    // A static catalogue, in the service's own words — read it once a session.
    staleTime: 60 * 60 * 1000,
    queryFn: async () => {
      const response = await axios.get<ServiceOptions>(
        buildApiUrl(APIS.USAGE_SERVICE_OPTIONS, {
          owner_id: ownerId as string,
        }),
      );
      return response.data ?? { service_types: [] };
    },
  });
}

/* ------------------------------------------------------------------ periods */

/** The three windows the period control offers, as the artifact names them. */
export const PERIODS = [
  { id: "7d", label: "Last 7 days", days: 7, bucket: "day" as TimeBucket },
  { id: "30d", label: "Last 30 days", days: 30, bucket: "day" as TimeBucket },
  { id: "90d", label: "Last 90 days", days: 90, bucket: "week" as TimeBucket },
] as const;

export type PeriodId = (typeof PERIODS)[number]["id"];

export const DEFAULT_PERIOD: PeriodId = "30d";

export function periodFor(id: PeriodId) {
  return PERIODS.find((period) => period.id === id) ?? PERIODS[1];
}

/** `YYYY-MM-DD`, which is what every date parameter on this service takes. */
export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * The window a period covers, and the one of the same length before it.
 *
 * The second is the whole point of the artifact's "compared with the previous
 * period of the same length" note: the deltas beside each figure are that
 * comparison, computed from two real answers rather than assumed.
 */
export function periodRanges(
  id: PeriodId,
  now: Date = new Date(),
): { current: DateRange; previous: DateRange } {
  const { days } = periodFor(id);
  const end = new Date(now);
  const start = new Date(end);
  start.setDate(start.getDate() - days);
  const previousEnd = new Date(start);
  const previousStart = new Date(previousEnd);
  previousStart.setDate(previousStart.getDate() - days);
  return {
    current: { start: isoDate(start), end: isoDate(end) },
    previous: { start: isoDate(previousStart), end: isoDate(previousEnd) },
  };
}

/**
 * Percent change between two figures, or `null` when there is nothing to
 * compare against — a delta of "+100%" against a previous period of zero says
 * more than the data does, so the screen shows no chevron at all instead.
 */
export function percentChange(
  current: number,
  previous: number,
): number | null {
  if (!previous) return null;
  return ((current - previous) / previous) * 100;
}
