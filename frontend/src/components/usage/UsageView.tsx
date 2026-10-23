"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import Icon from "@/components/Icon";
import { Card, EmptyNote, PageHead, Select } from "@/components/ui";
import { useCollections } from "@/data/collections";
import {
  DEFAULT_PERIOD,
  DEFAULT_SOURCE,
  labelChoices,
  operationChoices,
  operationLabeller,
  percentChange,
  periodFor,
  periodRanges,
  PERIODS,
  queryFilters,
  serviceLabeller,
  USAGE_SOURCES,
  useAnalytics,
  useServiceOptions,
  type AggregatedMetrics,
  type AnalyticsFilters,
  type AnalyticsResponse,
  type PeriodId,
  type UsageSource,
} from "@/data/usage";
import { ROUTES, TAB_PARAM } from "@/lib/routes";

import { SECTIONS } from "./constants";
import ModelTable from "./ModelTable";
import PlanLimits from "./PlanLimits";
import ServiceDonut from "./ServiceDonut";
import TrendCard from "./TrendCard";
import {
  comma,
  compact,
  Kpi,
  KpiGrid,
  MetricGroups,
  money,
  nextSort,
  Rail,
  Segmented,
  SortableTh,
  sortRows,
  TableShell,
  type SortState,
} from "./UsageBits";

/**
 * Usage & Limits.
 *
 * Everything on this screen is account-wide, because that is how the service
 * scopes it: one `analytics/query` answers the whole account, and the same
 * query with `group_by: ["knowledge_id"]` answers the per-collection
 * breakdown — so nothing here fans out one request per collection.
 *
 * The layout is the design artifact's: the limits strip, a period control with
 * the "compared with the previous period" note it promises, and three tabs.
 * The comparison is real — every period runs its query twice, once over the
 * window and once over the window of the same length before it — and so is the
 * cost, which the service breaks out rather than this screen estimating from a
 * per-page rate.
 */

type TabId = "overview" | "collections" | "limits" | "filters";

/**
 * The rail's three sections, and the head each one writes.
 *
 * The title changes with the section, as the artifact's does: the screen is
 * "Usage & Limits" from the board, but once you are inside it the heading names
 * the section you are reading rather than repeating where you came from.
 */

/** Pages the collections table counts: parsed document pages plus web pages. */
function pagesFromMetrics(metrics: AggregatedMetrics | undefined): number {
  return (
    (metrics?.llama_parse_page_count ?? 0) +
    (metrics?.processed_weblink_count ?? 0)
  );
}

const costOf = (metrics: AggregatedMetrics | undefined) =>
  metrics?.cost?.total_cost_usd ?? 0;

/**
 * The tab a link asked for, e.g. `?tab=limits` — how a quota refusal sends
 * someone straight to the allowance that refused them (see `lib/useApiError`).
 * Only the opening tab is read from the URL: switching tabs afterwards is a
 * local move, not a navigation to be pushed onto the history stack.
 */
function requestedTab(value: string | null): TabId {
  return SECTIONS.some((section) => section.id === value)
    ? (value as TabId)
    : "overview";
}

export default function UsageView() {
  const params = useSearchParams();
  const [period, setPeriod] = useState<PeriodId>(DEFAULT_PERIOD);
  const [tab, setTab] = useState<TabId>(() =>
    requestedTab(params.get(TAB_PARAM)),
  );
  const [source, setSource] = useState<UsageSource>(DEFAULT_SOURCE);
  const [operation, setOperation] = useState("");
  const [label, setLabel] = useState("");
  const [collectionId, setCollectionId] = useState("");

  const { collections } = useCollections();
  const { data: serviceOptions } = useServiceOptions();

  const { bucket, label: periodLabel } = periodFor(period);
  // Recomputed only when the period changes, so a re-render never shifts the
  // window under a query key and refetches everything.
  const ranges = useMemo(() => periodRanges(period), [period]);

  const onFilters = tab === "filters";
  // The Limits tab reads its own route, over its own windows: nothing on it is
  // narrowed by the period or the filters, so neither is fetched for it.
  const onLimits = tab === "limits";

  /** The Custom Filters tab's own selections, which belong to that tab alone. */
  const selections: AnalyticsFilters | undefined = useMemo(() => {
    const value: AnalyticsFilters = {};
    if (operation) value.operations = [operation];
    if (label) value.labels = [label];
    // One id, not a list — the service takes `knowledge_id` as a string, and
    // has only answered it since 2026-08-26.
    if (collectionId) value.knowledge_id = collectionId;
    return Object.keys(value).length ? value : undefined;
  }, [operation, label, collectionId]);

  /**
   * What every query on this screen carries.
   *
   * The Source control is the screen's, not one tab's: it sits beside the
   * period and narrows every section, so its `key_type` rides on all of them.
   * Total sends no `key_type` at all and is answered by its own unfiltered
   * call — it is never platform plus owner, because events that carry no key
   * type answer to neither filter and the sum comes up short.
   */
  const activeFilters = useMemo(
    () => queryFilters(onFilters ? selections : undefined, source),
    [onFilters, selections, source],
  );

  // Three queries, each with a job: the window, the window before it (the
  // deltas), and the per-collection split.
  //
  // The window query is deliberately *not* bucketed. The service returns
  // `knowledge_overview_metrics` — the files, characters, pages and vectors the
  // grouped tiles are made of — only on an unbucketed query; asking for a time
  // bucket here drops the whole block, which is what emptied Documents, Web and
  // Making documents searchable. The trend line asks for its own bucket.
  const current = useAnalytics(
    { date_range: ranges.current, group_by: [], filters: activeFilters },
    { enabled: !onLimits },
  );
  const previous = useAnalytics(
    { date_range: ranges.previous, group_by: [], filters: activeFilters },
    { enabled: !onLimits },
  );
  const byCollection = useAnalytics(
    {
      date_range: ranges.current,
      group_by: ["knowledge_id"],
      filters: activeFilters,
    },
    { enabled: tab === "collections" },
  );
  // Grouped by `service_type` and `operation`, as the service files them. The
  // service type is the same `knowledge` on every event this product records
  // today, so it splits nothing — it is carried because the answer carries it,
  // and the table names what did the work rather than leaving it implied.
  // The donut reads this same answer rather than asking for it again.
  const byOperation = useAnalytics(
    {
      date_range: ranges.current,
      group_by: ["service_type", "operation"],
      filters: activeFilters,
    },
    { enabled: tab !== "collections" && !onLimits },
  );

  // The pickers read from the catalogue *and* from the account's own grouped
  // usage, so they still offer real values when the catalogue cannot be read.
  const operations = operationChoices(serviceOptions, byOperation.data);
  const labels = labelChoices(serviceOptions, operation);
  const nameOfOperation = operationLabeller(serviceOptions);
  const nameOfService = serviceLabeller(serviceOptions);

  const now = current.data;
  const before = previous.data;
  const loading = current.isLoading;

  const overview = now?.knowledge_overview_metrics;

  const cost = now?.totals.cost;

  /**
   * The four figures the artifact leads with, each carrying what it cost.
   *
   * Requests has no money beside it because the service prices tokens and work,
   * not calls — a cost on the request count would be one of the other three
   * repeated under a third name.
   */
  const kpis = [
    {
      label: "Total tokens",
      value: compact(now?.totals.total_tokens ?? 0),
      cost: cost?.llm_cost_usd,
      delta: percentChange(
        now?.totals.total_tokens ?? 0,
        before?.totals.total_tokens ?? 0,
      ),
      foot: periodLabel,
    },
    {
      label: "Input tokens",
      value: compact(now?.totals.input_tokens ?? 0),
      cost: cost?.llm_input_tokens_cost_usd,
      delta: percentChange(
        now?.totals.input_tokens ?? 0,
        before?.totals.input_tokens ?? 0,
      ),
      foot: periodLabel,
    },
    {
      label: "Output tokens",
      value: compact(now?.totals.output_tokens ?? 0),
      cost: cost?.llm_output_tokens_cost_usd,
      delta: percentChange(
        now?.totals.output_tokens ?? 0,
        before?.totals.output_tokens ?? 0,
      ),
      foot: periodLabel,
    },
    {
      label: "Requests",
      value: compact(now?.totals.request_count ?? 0),
      cost: undefined,
      delta: percentChange(
        now?.totals.request_count ?? 0,
        before?.totals.request_count ?? 0,
      ),
      foot: periodLabel,
    },
  ];

  /**
   * The secondary figures, in the artifact's three groups.
   *
   * Cost is attached only where the service attributes one: its breakdown
   * prices document processing, embedding and transcription as single lines, so
   * the money hangs on the figure that line is actually about rather than being
   * repeated across every tile in the group.
   */
  const groups = [
    {
      heading: "Documents",
      metrics: [
        {
          key: "files",
          label: "Files processed",
          value: overview?.total_files_processed ?? null,
          unit: "files",
        },
        {
          key: "characters",
          label: "Characters read",
          value: overview?.total_char_count ?? null,
          unit: "characters",
        },
        {
          key: "llamaparse",
          label: "LlamaParse pages",
          value:
            overview?.total_llama_parse_pages ??
            now?.totals.llama_parse_page_count ??
            null,
          unit: "pages",
          cost: cost?.document_processing_cost_usd,
        },
      ],
    },
    {
      heading: "Web",
      metrics: [
        {
          key: "weblinks",
          label: "Website pages read",
          value:
            overview?.total_weblink_pages ??
            now?.totals.processed_weblink_count ??
            null,
          unit: "pages",
        },
      ],
    },
    {
      heading: "Making documents searchable",
      metrics: [
        {
          key: "embedding-tokens",
          label: "Embedding tokens",
          value: overview?.total_embedding_tokens ?? null,
          unit: "tokens",
          cost: cost?.embedding_cost_usd,
        },
        {
          key: "vectors",
          label: "Searchable sections",
          value: overview?.total_vectors_indexed ?? null,
          unit: "sections",
        },
        {
          key: "text",
          label: "Text sources",
          value: overview?.total_text_count ?? null,
          unit: "pasted and extracted texts",
        },
      ],
    },
  ];

  const section = SECTIONS.find((entry) => entry.id === tab) ?? SECTIONS[0];
  /**
   * Which reading is on screen, said in words wherever a figure is quoted.
   *
   * Total says nothing, because Total is the whole window and needs no caveat;
   * the other two are a subset of it, and a figure that does not say so reads
   * as the account's whole spend.
   */
  const sourceLabel = USAGE_SOURCES.find((entry) => entry.id === source)?.label;
  const sourceNote = source === "total" ? "" : ` · ${sourceLabel} only`;
  const subtitle =
    tab === "collections"
      ? `Consumption and estimated cost, one row per document collection${sourceNote}.`
      : tab === "limits"
        ? "What this account is allowed to use, and how much of it is left."
        : tab === "filters"
          ? `Narrow the period down to one operation, label or collection${sourceNote}.`
          : `${comma(now?.totals.request_count ?? 0)} requests · ${periodLabel}${sourceNote}`;

  return (
    <div className="flex flex-col items-start gap-5 lg:flex-row">
      <Rail<TabId>
        label="Usage sections"
        heading={
          <span className="flex items-center gap-2 text-[13px] font-bold text-accent">
            <svg
              viewBox="0 0 20 20"
              width="16"
              height="16"
              fill="none"
              aria-hidden="true"
            >
              <path
                d="M2.5 12.5l3.5-5 3 3.5 3.5-6 5 7.5"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Usage
          </span>
        }
        items={SECTIONS}
        value={tab}
        onChange={setTab}
        back={
          <Link
            href={ROUTES.home}
            className="inline-flex items-center gap-1.5 px-2 py-1 text-[13px] font-semibold text-accent no-underline hover:underline"
          >
            <Icon name="caretLeft" className="size-3.5" />
            Back to all collections
          </Link>
        }
      />

      <div className="min-w-0 flex-1">
        <PageHead title={section.title} sub={subtitle} />

        {/* The reporting period is not offered on the Limits tab: every row
            there is counted over its own fixed window — what is held right
            now, the day, the month, the account's lifetime — so a period
            control beside it would claim to move a window it cannot. */}
        {!onLimits && (
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Segmented<PeriodId>
              label="Reporting period"
              options={PERIODS.map((entry) => ({
                id: entry.id,
                label: entry.label,
              }))}
              value={period}
              onChange={setPeriod}
            />
            {/* The Source control sits with the period because it does the
                same job: both narrow every section of the screen at once, and
                neither belongs to the tab that happens to be open. */}
            <label className="flex items-center gap-2 text-[12px] text-ink-2">
              Consumption
              <Select
                value={source}
                aria-label="Consumption source"
                onChange={(event) =>
                  setSource(event.target.value as UsageSource)
                }
              >
                {USAGE_SOURCES.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </label>
            <span className="text-[12px] text-ink-3">
              Applies to every section on this screen, and is compared with the
              previous period of the same length.
            </span>
          </div>
        )}

        {onLimits ? (
          <PlanLimits />
        ) : current.isError ? (
          <EmptyNote>
            Usage could not be read for this account. Try again in a moment.
          </EmptyNote>
        ) : tab === "collections" ? (
          <CollectionsTab
            response={byCollection.data}
            isLoading={byCollection.isLoading}
            names={collections}
            periodLabel={periodLabel}
          />
        ) : (
          <>
            {/* Equal columns, as the artifact lays them out — the controls
                read as one filter, not as four of different width. */}
            {tab === "filters" && (
              <Card className="mb-4 grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
                {/* No Service picker: `service_type` is `knowledge` on every
                    event this product records, so the only thing it could
                    narrow to is everything. Which key paid is the screen's
                    Source control, above, not a fourth box here. */}
                <label className="flex flex-col gap-1 text-[12px] text-ink-2">
                  Operation
                  <Select
                    value={operation}
                    aria-label="Operation"
                    onChange={(event) => {
                      setOperation(event.target.value);
                      // The labels listed belong to the operation above, so a
                      // change here would otherwise leave a label selected that
                      // the new operation never records.
                      setLabel("");
                    }}
                  >
                    <option value="">All operations</option>
                    {operations.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                </label>
                <label className="flex flex-col gap-1 text-[12px] text-ink-2">
                  Functional label
                  <Select
                    value={label}
                    aria-label="Functional label"
                    onChange={(event) => setLabel(event.target.value)}
                  >
                    <option value="">All labels</option>
                    {labels.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                </label>
                {/* Named from the board's own collections rather than from the
                    usage answer: a collection that spent nothing in this window
                    still belongs in the picker, and picking it is how you find
                    out that it spent nothing. */}
                <label className="flex flex-col gap-1 text-[12px] text-ink-2">
                  Collection
                  <Select
                    value={collectionId}
                    aria-label="Collection"
                    onChange={(event) => setCollectionId(event.target.value)}
                  >
                    <option value="">All collections</option>
                    {collections.map((collection) => (
                      <option key={collection.id} value={collection.id}>
                        {collection.name}
                      </option>
                    ))}
                  </Select>
                </label>
              </Card>
            )}

            <KpiGrid>
              {kpis.map((kpi) => (
                <Kpi
                  key={kpi.label}
                  label={kpi.label}
                  value={kpi.value}
                  cost={kpi.cost}
                  delta={kpi.delta}
                  foot={kpi.foot}
                  loading={loading}
                />
              ))}
            </KpiGrid>

            <MetricGroups
              groups={groups}
              foot={periodLabel.toLowerCase()}
              loading={loading}
            />

            {/* 70 / 30, as the artifact splits them: the line needs the width to
                stay readable across ninety buckets, the ring does not. */}
            <div className="grid gap-4 xl:grid-cols-[7fr_3fr]">
              <TrendCard
                range={ranges.current}
                filters={activeFilters}
                periodLabel={periodLabel}
                defaultBucket={bucket}
              />
              <ServiceDonut
                response={byOperation.data}
                isLoading={byOperation.isLoading}
              />
            </div>

            <ModelTable
              range={ranges.current}
              filters={activeFilters}
              periodLabel={periodLabel}
            />

            <OperationTable
              response={byOperation.data}
              isLoading={byOperation.isLoading}
              nameOf={nameOfOperation}
              nameOfService={nameOfService}
              periodLabel={periodLabel}
            />
          </>
        )}
      </div>
    </div>
  );
}

/* ----------------------------------------------------- per-operation table */

type OperationKey = "service" | "operation" | "requests" | "tokens" | "cost";

/**
 * What the account spent, by operation.
 *
 * The artifact breaks its usage down by the four steps of answering a question;
 * the service records operations instead — reading a document, calling a model,
 * embedding it, writing the vectors, generating the answer — so those are the
 * rows. The same table, reading what the account actually did rather than a
 * shape invented for the mock.
 *
 * Each row is named by the catalogue's `display_name` for its operation, not by
 * a humanised version of the code: the service publishes those names, and a
 * second spelling invented here would disagree with the picker that files them.
 *
 * Service is the other half of the grouping the answer comes back with. It
 * reads `Knowledge` down the whole column for this product — that is what the
 * service records — and the column is here because the row is the service's,
 * not this screen's: what did the work is stated rather than assumed.
 */
function OperationTable({
  response,
  isLoading,
  nameOf,
  nameOfService,
  periodLabel,
}: {
  response: AnalyticsResponse | undefined;
  isLoading: boolean;
  nameOf: (operation: string | undefined) => string;
  nameOfService: (serviceType: string | undefined) => string;
  periodLabel: string;
}) {
  const [sort, setSort] = useState<SortState<OperationKey> | null>(null);

  const rows = (response?.data ?? []).map((point) => ({
    // Both halves of the grouping: one operation can be recorded under more
    // than one service, and a key of the operation alone would collide.
    key: `${point.dimensions.service_type ?? "—"}/${point.dimensions.operation ?? "—"}`,
    service: nameOfService(point.dimensions.service_type),
    operation: nameOf(point.dimensions.operation),
    requests: point.metrics.request_count ?? 0,
    tokens: point.metrics.total_tokens ?? 0,
    cost: costOf(point.metrics),
  }));

  const totalCost = rows.reduce((sum, row) => sum + row.cost, 0);
  const totalRequests = rows.reduce((sum, row) => sum + row.requests, 0);
  const sorted = sortRows(rows, sort);

  return (
    <TableShell
      title="By operation"
      note={`${periodLabel} · click a column heading to sort`}
      footer={
        <>
          <span>
            {rows.length} {rows.length === 1 ? "row" : "rows"}
            {sort ? ` · sorted by ${sort.key} ${sort.dir}` : " · unsorted"}
          </span>
          <span>
            {comma(totalRequests)} requests · {money(totalCost)}
          </span>
        </>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-line">
              <SortableTh
                columnKey="service"
                label="Service"
                sort={sort}
                onSort={(key) => setSort(nextSort(sort, key))}
              />
              <SortableTh
                columnKey="operation"
                label="Operation"
                sort={sort}
                onSort={(key) => setSort(nextSort(sort, key))}
              />
              <SortableTh
                columnKey="requests"
                label="Requests"
                sort={sort}
                onSort={(key) => setSort(nextSort(sort, key))}
                numeric
              />
              <SortableTh
                columnKey="tokens"
                label="Tokens"
                sort={sort}
                onSort={(key) => setSort(nextSort(sort, key))}
                numeric
              />
              <SortableTh
                columnKey="cost"
                label="Cost"
                sort={sort}
                onSort={(key) => setSort(nextSort(sort, key))}
                numeric
              />
              <th className="p-3 text-right text-[13px] font-semibold text-ink-2">
                % of cost
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-4 text-ink-2">
                  {isLoading
                    ? "Reading…"
                    : "Nothing was recorded in this window."}
                </td>
              </tr>
            ) : (
              sorted.map((row) => (
                <tr key={row.key} className="border-b border-line-soft">
                  <td className="p-3 text-ink-2">{row.service}</td>
                  <td className="p-3 font-semibold text-ink">
                    {row.operation}
                  </td>
                  <td className="p-3 text-right text-ink-2 tabular-nums">
                    {comma(row.requests)}
                  </td>
                  <td className="p-3 text-right text-ink-2 tabular-nums">
                    {comma(row.tokens)}
                  </td>
                  <td className="p-3 text-right text-ink-2 tabular-nums">
                    {money(row.cost)}
                  </td>
                  <td className="p-3 text-right text-ink-2 tabular-nums">
                    {totalCost
                      ? `${Math.round((row.cost / totalCost) * 100)}%`
                      : "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </TableShell>
  );
}

/* ---------------------------------------------------------- collections tab */

type CollectionKey = "name" | "id" | "requests" | "pages" | "tokens" | "cost";

/**
 * The account's spend split by collection.
 *
 * `group_by: ["knowledge_id"]` is the service's own answer to this question, so
 * the ids come back rather than the names — the board already knows the names,
 * and a collection that has spent nothing simply does not appear.
 */
function CollectionsTab({
  response,
  isLoading,
  names,
  periodLabel,
}: {
  response: AnalyticsResponse | undefined;
  isLoading: boolean;
  names: Array<{ id: string; name: string }>;
  periodLabel: string;
}) {
  const [sort, setSort] = useState<SortState<CollectionKey> | null>(null);

  const nameOf = (id: string) =>
    names.find((collection) => collection.id === id)?.name ?? "Unnamed";

  const rows = (response?.data ?? []).map((point) => {
    const id = point.dimensions.knowledge_id ?? "—";
    return {
      name: nameOf(id),
      id,
      requests: point.metrics.request_count ?? 0,
      pages: pagesFromMetrics(point.metrics),
      tokens: point.metrics.total_tokens ?? 0,
      cost: costOf(point.metrics),
    };
  });

  const sorted = sortRows(rows, sort);
  const totals = rows.reduce(
    (sum, row) => ({
      requests: sum.requests + row.requests,
      pages: sum.pages + row.pages,
      cost: sum.cost + row.cost,
    }),
    { requests: 0, pages: 0, cost: 0 },
  );

  const onSort = (key: CollectionKey) => setSort(nextSort(sort, key));

  return (
    <TableShell
      title="Every collection"
      note={`${periodLabel} · click a column heading to sort`}
      footer={
        <>
          <span>
            {rows.length} {rows.length === 1 ? "collection" : "collections"}
            {sort ? ` · sorted by ${sort.key} ${sort.dir}` : " · unsorted"}
          </span>
          <span>
            {comma(totals.requests)} requests · {comma(totals.pages)} pages ·{" "}
            {money(totals.cost)}
          </span>
        </>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-line">
              <SortableTh
                columnKey="name"
                label="Collection"
                sort={sort}
                onSort={onSort}
              />
              <SortableTh
                columnKey="id"
                label="Knowledge ID"
                sort={sort}
                onSort={onSort}
              />
              <SortableTh
                columnKey="requests"
                label="Requests"
                sort={sort}
                onSort={onSort}
                numeric
              />
              <SortableTh
                columnKey="pages"
                label="Pages read"
                sort={sort}
                onSort={onSort}
                numeric
              />
              <SortableTh
                columnKey="tokens"
                label="Tokens"
                sort={sort}
                onSort={onSort}
                numeric
              />
              <SortableTh
                columnKey="cost"
                label="Cost"
                sort={sort}
                onSort={onSort}
                numeric
              />
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-4 text-ink-2">
                  {isLoading
                    ? "Reading…"
                    : "No collection recorded anything in this window."}
                </td>
              </tr>
            ) : (
              sorted.map((row) => (
                <tr key={row.id} className="border-b border-line-soft">
                  <td className="p-3 font-semibold text-ink">{row.name}</td>
                  <td className="p-3 font-mono text-[11px] text-ink-3">
                    {row.id}
                  </td>
                  <td className="p-3 text-right text-ink-2 tabular-nums">
                    {comma(row.requests)}
                  </td>
                  <td className="p-3 text-right text-ink-2 tabular-nums">
                    {comma(row.pages)}
                  </td>
                  <td className="p-3 text-right text-ink-2 tabular-nums">
                    {comma(row.tokens)}
                  </td>
                  <td className="p-3 text-right text-ink-2 tabular-nums">
                    {money(row.cost)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </TableShell>
  );
}
