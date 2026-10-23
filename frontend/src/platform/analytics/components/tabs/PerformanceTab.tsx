"use client";

import { useState } from "react";
import dynamic from "next/dynamic";

import type { UseQueryResult } from "@tanstack/react-query";
import { Clock, TrendingDown, TrendingUp } from "lucide-react";

import { useAnalyticsScope } from "../../AnalyticsScope";
import { useCardJump } from "../../hooks/useCardJump";
import { useQueryLog } from "../../hooks/useQueryLog";
import { useTrends } from "../../hooks/useTrends";
import { deriveLatencyDistribution } from "../../lib/deriveLatencyDistribution";
import { deriveNodeStats } from "../../lib/deriveNodeStats";
import type { ScorecardDeltas } from "../../lib/scorecardDeltas";
import { toQueryLogFilters } from "../../lib/toQueryLogFilters";
import type { AnalyticsWindow, Scorecard } from "../../types";
import ChartCard from "../ChartCard";
import DataState from "../DataState";
import NodeBreakdownTable from "../NodeBreakdownTable";
import PerfKpiCard from "../PerfKpiCard";
import QueryFilterBar, { type QueryFilterValue } from "../QueryFilterBar";
import SlowestQueriesTable, {
  type SlowestSortKey,
} from "../tables/SlowestQueriesTable";

const LatencyTrendChart = dynamic(() => import("../charts/LatencyTrendChart"), {
  ssr: false,
});
const ResponseTimeDistributionChart = dynamic(
  () => import("../charts/ResponseTimeDistributionChart"),
  { ssr: false },
);

const fmtMs = (ms?: number) =>
  ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`;

export default function PerformanceTab({
  scorecard,
  window,
  agentId,
  deltas,
}: {
  scorecard: UseQueryResult<Scorecard>;
  window: AnalyticsWindow;
  agentId?: string | null;
  deltas?: ScorecardDeltas;
}) {
  const sc = scorecard.data;
  const { limits } = useAnalyticsScope();

  // Numbered-pagination state for slowest table
  const [pageIndex, setPageIndex] = useState(0);
  // Sized from `top_n`, the parameter that sizes `perf.slowest` in the
  // scorecard this table sits beside.
  const [pageSize, setPageSize] = useState(limits.scorecardTopN);

  // Filter state for slowest table
  const [filterValue, setFilterValue] = useState<QueryFilterValue>({});

  // Column sort for the slowest table (default: latency desc)
  const [sort, setSort] = useState<SlowestSortKey>("latency_ms");
  const [order, setOrder] = useState<"asc" | "desc">("desc");

  function handleSort(col: SlowestSortKey) {
    if (col === sort) {
      setOrder((o) => (o === "desc" ? "asc" : "desc"));
    } else {
      setSort(col);
      setOrder("desc");
    }
    setPageIndex(0);
  }

  // Card-jump: KPI cards scroll to the slowest queries section
  const { ref: slowestRef, jump } = useCardJump();

  const trends = useTrends(agentId, window, {
    bucket: "day",
    metrics: ["avg_ms", "p95_ms"],
  });

  // Slowest queries fetch (filtered + load-more limit)
  const slowest = useQueryLog(agentId, window, {
    sort,
    order,
    limit: pageSize,
    offset: pageIndex * pageSize,
    filters: toQueryLogFilters(filterValue),
  });

  // Distribution sample: most recent 100 queries (API caps `limit` at 100)
  const sample = useQueryLog(agentId, window, {
    sort: "created_at",
    order: "desc",
    limit: 100,
  });

  const distributionData = deriveLatencyDistribution(
    sample.data?.queries?.map((q) => q.metrics) ?? [],
  );
  const hasDistributionData = (sample.data?.queries?.length ?? 0) > 0;

  // Per-node latency/cost aggregated from the same recent-100 sample (no
  // per-node rollup endpoint — derived from each query's service_calls/node_data).
  const nodeStats = deriveNodeStats(sample.data?.queries ?? []);

  const series = trends.data?.series ?? [];

  const slowestTotal = slowest.data?.total_matching ?? 0;

  return (
    <div className="space-y-4">
      {/* KPI cards — clicking scrolls to the Slowest Queries section */}
      <div className="grid grid-cols-3 gap-4 max-md:grid-cols-1">
        <button
          type="button"
          aria-label="Avg Response Time — jump to slowest queries"
          className="h-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-lg"
          onClick={jump}
        >
          <PerfKpiCard
            label="Avg Response Time"
            value={fmtMs(sc?.perf.avg_ms)}
            delta={deltas?.avg_ms ? Number(deltas.avg_ms.pct.toFixed(1)) : null}
            goodWhen="down"
            icon={<Clock className="size-4 text-amber-500" />}
            iconBg="bg-amber-50"
            isLoading={scorecard.isLoading}
          />
        </button>
        <button
          type="button"
          aria-label="P50 Latency — jump to slowest queries"
          className="h-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-lg"
          onClick={jump}
        >
          <PerfKpiCard
            label="P50 Latency"
            value={fmtMs(sc?.perf.p50_ms)}
            delta={deltas?.p50_ms ? Number(deltas.p50_ms.pct.toFixed(1)) : null}
            goodWhen="down"
            icon={<TrendingUp className="size-4 text-emerald-500" />}
            iconBg="bg-emerald-50"
            isLoading={scorecard.isLoading}
          />
        </button>
        <button
          type="button"
          aria-label="P95 Latency — jump to slowest queries"
          className="h-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-lg"
          onClick={jump}
        >
          <PerfKpiCard
            label="P95 Latency"
            value={fmtMs(sc?.perf.p95_ms)}
            valueColor="text-amber-500"
            delta={deltas?.p95_ms ? Number(deltas.p95_ms.pct.toFixed(1)) : null}
            goodWhen="down"
            icon={<TrendingDown className="size-4 text-red-500" />}
            iconBg="bg-red-50"
            isLoading={scorecard.isLoading}
          />
        </button>
      </div>

      {/* Per-node / service breakdown — between the cards and the charts */}
      <NodeBreakdownTable
        stats={nodeStats}
        sampleSize={sample.data?.queries?.length ?? 0}
        isLoading={sample.isLoading}
        isError={sample.isError}
      />

      {/* Trend + distribution */}
      <div className="flex gap-4 max-md:flex-col">
        <ChartCard
          title="Latency Trend"
          subtitle="Avg & P95 daily — current vs previous period"
          className="h-[300px]"
        >
          <DataState
            isLoading={trends.isLoading}
            isError={trends.isError}
            isEmpty={!series.length}
          >
            <LatencyTrendChart data={series} />
          </DataState>
        </ChartCard>
        <ChartCard
          title="Response Time Distribution"
          subtitle="How many queries fell in each latency bucket"
          className="h-[300px]"
        >
          <p className="text-[11px] text-slate-400 mb-1">
            Based on the most recent 100 queries
          </p>
          <DataState
            isLoading={sample.isLoading}
            isError={sample.isError}
            isEmpty={!hasDistributionData}
          >
            <ResponseTimeDistributionChart data={distributionData} />
          </DataState>
        </ChartCard>
      </div>

      {/* Slowest queries — filter bar + load-more */}
      <div ref={slowestRef} className="space-y-3">
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          {/* Section header */}
          <div className="px-4 py-3 border-b border-slate-100">
            <h4 className="text-sm font-semibold text-slate-700">
              Slowest Queries
            </h4>
            <p className="text-xs text-slate-400 mt-0.5">
              Top queries by response time — expand row for details
            </p>
          </div>

          {/* Filter bar */}
          <div className="px-4 pt-4 pb-2">
            <QueryFilterBar
              value={filterValue}
              onChange={(next) => {
                setFilterValue(next);
                setPageIndex(0);
              }}
            />
          </div>

          {/* Table (presentational, receives rows as props). The table renders
              its own numbered-pagination footer + count when onPageChange is set. */}
          <SlowestQueriesTable
            rows={slowest.data?.queries ?? []}
            isLoading={slowest.isLoading}
            total={slowestTotal}
            pageIndex={pageIndex}
            pageSize={pageSize}
            onPageChange={(p) => {
              setPageIndex(p.pageIndex);
              setPageSize(p.pageSize);
            }}
            search={filterValue.search ?? ""}
            sort={sort}
            order={order}
            onSort={handleSort}
          />
        </div>
      </div>
    </div>
  );
}
