"use client";

import { useState } from "react";
import dynamic from "next/dynamic";

import type { UseQueryResult } from "@tanstack/react-query";
import { BarChart2, DollarSign, RefreshCw } from "lucide-react";
import { Cell, Pie, PieChart, Tooltip } from "recharts";

import { useToast } from "@/platform/components/ToastContext";
import { cn } from "@/platform/utils";

import { useAnalyticsScope } from "../../AnalyticsScope";
import { useCardJump } from "../../hooks/useCardJump";
import { useQueryLog } from "../../hooks/useQueryLog";
import { useSyncCosts } from "../../hooks/useSyncCosts";
import { useTrends } from "../../hooks/useTrends";
import { deriveNodeStats } from "../../lib/deriveNodeStats";
import type { ScorecardDeltas } from "../../lib/scorecardDeltas";
import { toQueryLogFilters } from "../../lib/toQueryLogFilters";
import type { AnalyticsWindow, Scorecard } from "../../types";
import ChartCard from "../ChartCard";
import CostKpiCard from "../CostKpiCard";
import DataState from "../DataState";
import NodeBreakdownTable from "../NodeBreakdownTable";
import QueryFilterBar, { type QueryFilterValue } from "../QueryFilterBar";
import MostExpensiveTable, {
  type ExpensiveSort,
} from "../tables/MostExpensiveTable";

const CostTrendChart = dynamic(() => import("../charts/CostTrendChart"), {
  ssr: false,
});

const DONUT_COLORS = ["#6366f1", "#22c55e", "#f59e0b"];

const fmtUsd = (n?: number) => (n == null ? "—" : `$${n.toFixed(2)}`);
const fmtTokens = (n?: number | null) => {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
};

export default function CostTab({
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
  const { filterOptions, limits } = useAnalyticsScope();

  // Numbered-pagination state for expensive table
  const [pageIndex, setPageIndex] = useState(0);
  // Sized from `top_n`, the same parameter that sizes `cost.most_expensive`
  // in the scorecard this table sits beside — one number, from the backend.
  const [pageSize, setPageSize] = useState(limits.scorecardTopN);
  // Empty means "whatever the service defaults to" — see the select below.
  const [syncStrategy, setSyncStrategy] = useState("");

  // Filter state for expensive table
  const [filterValue, setFilterValue] = useState<QueryFilterValue>({});

  const [sort, setSort] = useState<ExpensiveSort>("cost_usd");
  const [order, setOrder] = useState<"asc" | "desc">("desc");

  function handleSort(col: ExpensiveSort) {
    if (col === sort) {
      setOrder((o) => (o === "desc" ? "asc" : "desc"));
    } else {
      setSort(col);
      setOrder("desc");
    }
    setPageIndex(0);
  }

  // Anchor for the Most Expensive Queries section (KPI cards are display-only).
  const { ref: expensiveRef } = useCardJump();

  const trends = useTrends(agentId, window, {
    bucket: "day",
    metrics: ["cost"],
  });
  const expensive = useQueryLog(agentId, window, {
    sort,
    order,
    limit: pageSize,
    offset: pageIndex * pageSize,
    filters: toQueryLogFilters(filterValue),
  });
  const sync = useSyncCosts(agentId, window);
  const { toast } = useToast();
  const costIncomplete = sc?.cost.cost_complete === false;

  // Recent-100 sample (API caps `limit` at 100) — powers the per-node cost
  // table and the cost-by-node donut client-side (no rollup endpoint).
  const sample = useQueryLog(agentId, window, {
    sort: "created_at",
    order: "desc",
    limit: 100,
  });
  const nodeStats = deriveNodeStats(sample.data?.queries ?? []);

  // Donut: top two nodes by cost share + "others" bucket.
  const byCost = [...nodeStats]
    .filter((n) => n.cost > 0)
    .sort((a, b) => b.cost - a.cost);
  const donutData = byCost.length
    ? [
        ...byCost.slice(0, 2).map((n, i) => ({
          name: n.node,
          value: n.cost_pct,
          color: DONUT_COLORS[i],
        })),
        ...(byCost.length > 2
          ? [
              {
                name: "others",
                value: byCost.slice(2).reduce((a, n) => a + n.cost_pct, 0),
                color: DONUT_COLORS[2],
              },
            ]
          : []),
      ]
    : [];

  const series = trends.data?.series ?? [];

  // % change vs previous period for the Total Cost pill (computed in page.tsx).
  const costPct = deltas?.total_cost ?? null;
  const totalQueries = sc?.usage?.total_queries;
  const avgInput =
    sc?.cost.input_tokens && totalQueries
      ? Math.round(sc.cost.input_tokens / totalQueries).toLocaleString()
      : "—";
  const avgOutput =
    sc?.cost.output_tokens && totalQueries
      ? Math.round(sc.cost.output_tokens / totalQueries).toLocaleString()
      : "—";
  const avgTotal =
    sc?.cost.total_tokens && totalQueries
      ? Math.round(sc.cost.total_tokens / totalQueries).toLocaleString()
      : "—";

  const expensiveTotal = expensive.data?.total_matching ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end gap-2">
        {/*
          Which reconciliation to run, when the backend publishes a choice. The
          default is the server's own — the select starts empty and sends no
          `strategy` at all, so this control adds a capability without changing
          what the button did before it existed.
        */}
        {filterOptions.costStrategies.length > 0 && (
          <label className="flex items-center gap-1.5 text-xs text-slate-500">
            Strategy
            <select
              aria-label="Cost sync strategy"
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 focus:border-indigo-500 focus:outline-none"
              value={syncStrategy}
              onChange={(e) => setSyncStrategy(e.target.value)}
              disabled={sync.isPending}
            >
              <option value="">Default</option>
              {filterOptions.costStrategies.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <button
          type="button"
          onClick={() =>
            sync.mutate(
              { strategy: syncStrategy || undefined },
              {
                onSuccess: () => toast.success("Costs synced."),
                onError: () => toast.error("Failed to sync costs."),
              },
            )
          }
          disabled={sync.isPending}
          className={cn(
            "inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50",
            sync.isPending && "cursor-not-allowed opacity-60",
          )}
        >
          <RefreshCw
            className={cn("size-3.5", sync.isPending && "animate-spin")}
          />
          {sync.isPending ? "Refreshing…" : "Refresh costs"}
        </button>
      </div>

      {costIncomplete && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
          Some queries are unpriced — figures are a lower bound. Refresh to sync
          LangSmith costs.
        </div>
      )}

      {/* KPI cards (display-only) */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <div className="h-full">
          <CostKpiCard
            label="Total Cost"
            value={fmtUsd(sc?.cost.total_cost)}
            hint="this period"
            deltaLabel={costPct ? `${costPct.abs}%` : null}
            deltaDirection={costPct?.direction === "down" ? "down" : "up"}
            goodWhen="down"
            icon={<DollarSign className="size-4 text-indigo-500" />}
            iconBg="bg-indigo-50"
            isLoading={scorecard.isLoading}
          />
        </div>
        <div className="h-full">
          <CostKpiCard
            label="Avg Cost / Query"
            value={
              sc?.cost.avg_cost_per_query != null
                ? `$${sc.cost.avg_cost_per_query.toFixed(3)}`
                : "—"
            }
            hint="per query avg"
            deltaLabel={
              deltas?.avg_cost_per_query
                ? `${deltas.avg_cost_per_query.abs}%`
                : null
            }
            deltaDirection={
              deltas?.avg_cost_per_query?.direction === "down" ? "down" : "up"
            }
            goodWhen="down"
            icon={<BarChart2 className="size-4 text-amber-500" />}
            iconBg="bg-amber-50"
            isLoading={scorecard.isLoading}
          />
        </div>

        {/* Token Consumption split card */}
        <div className="h-full rounded-lg border border-gray-200 bg-white shadow-sm p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Token Consumption
            </div>
            {deltas?.total_tokens &&
              deltas.total_tokens.direction !== "flat" && (
                <span
                  className={cn(
                    "inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11px] font-semibold",
                    deltas.total_tokens.direction === "up"
                      ? "bg-amber-50 text-amber-700"
                      : "bg-emerald-50 text-emerald-700",
                  )}
                >
                  {deltas.total_tokens.direction === "up" ? "↑" : "↓"}
                  {deltas.total_tokens.abs}% vs prev
                </span>
              )}
          </div>
          {scorecard.isLoading ? (
            <div className="space-y-2">
              <div className="h-14 rounded-md bg-slate-100 animate-pulse" />
              <div className="grid grid-cols-2 gap-2">
                <div className="h-20 rounded-md bg-slate-100 animate-pulse" />
                <div className="h-20 rounded-md bg-slate-100 animate-pulse" />
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="rounded-md bg-indigo-50 p-2.5">
                <div className="flex items-baseline justify-between">
                  <div className="text-[10px] font-medium uppercase text-indigo-700">
                    Total Tokens (input + output)
                  </div>
                  <div className="text-[10px] text-slate-600">
                    Avg {avgTotal} / query
                  </div>
                </div>
                <div className="mt-0.5 text-lg font-bold text-slate-900">
                  {fmtTokens(sc?.cost.total_tokens)}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-md bg-amber-50 p-2.5">
                  <div className="text-[10px] font-medium uppercase text-amber-700">
                    Total Input
                  </div>
                  <div className="mt-0.5 text-lg font-bold text-slate-900">
                    {fmtTokens(sc?.cost.input_tokens)}
                  </div>
                  <div className="mt-1 text-[10px] text-slate-600">
                    Avg {avgInput} / query
                  </div>
                </div>
                <div className="rounded-md bg-emerald-50 p-2.5">
                  <div className="text-[10px] font-medium uppercase text-emerald-700">
                    Total Output
                  </div>
                  <div className="mt-0.5 text-lg font-bold text-slate-900">
                    {fmtTokens(sc?.cost.output_tokens)}
                  </div>
                  <div className="mt-1 text-[10px] text-slate-600">
                    Avg {avgOutput} / query
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Donut: cost share by top node (recent-100 sample) */}
        <div className="h-full rounded-lg border border-gray-200 bg-white shadow-sm p-4 flex items-center justify-center gap-3">
          {sample.isLoading ? (
            <div className="size-20 rounded-full bg-slate-100 animate-pulse" />
          ) : donutData.length ? (
            <>
              <div
                className="relative shrink-0"
                style={{ width: 96, height: 96 }}
              >
                <PieChart width={96} height={96}>
                  <Pie
                    data={donutData}
                    cx="50%"
                    cy="50%"
                    innerRadius={32}
                    outerRadius={45}
                    paddingAngle={3}
                    dataKey="value"
                    startAngle={90}
                    endAngle={-270}
                    strokeWidth={0}
                  >
                    {donutData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(v: number) => `${v.toFixed(1)}%`}
                    contentStyle={{
                      backgroundColor: "white",
                      border: "1px solid #e5e7eb",
                      borderRadius: "6px",
                      padding: "6px 10px",
                      fontSize: 12,
                    }}
                  />
                </PieChart>
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                  <span className="text-xs font-bold text-slate-900">
                    {fmtUsd(sc?.cost.total_cost)}
                  </span>
                </div>
              </div>
              <div className="space-y-1.5 min-w-0">
                {donutData.map((d) => (
                  <div key={d.name} className="flex items-center gap-1.5">
                    <div
                      className="size-2 rounded-full shrink-0"
                      style={{ backgroundColor: d.color }}
                    />
                    <span className="text-xs font-bold text-slate-700 tabular-nums">
                      {d.value.toFixed(0)}%
                    </span>
                    <span className="truncate text-xs text-slate-500">
                      {d.name}
                    </span>
                  </div>
                ))}
                <p className="text-[10px] text-slate-400">
                  Cost share by node · recent 100
                </p>
              </div>
            </>
          ) : (
            <div className="w-full flex items-center justify-center text-xs text-slate-400">
              No per-node cost data
            </div>
          )}
        </div>
      </div>

      {/* Per-node / service cost breakdown — between the cards and the trend */}
      <NodeBreakdownTable
        stats={nodeStats}
        sampleSize={sample.data?.queries?.length ?? 0}
        isLoading={sample.isLoading}
        isError={sample.isError}
        sortBy="cost"
      />

      {/* Cost trend */}
      <ChartCard
        title="Cost Trend"
        subtitle="Daily total cost and avg cost per query"
        className="h-[300px]"
      >
        <DataState
          isLoading={trends.isLoading}
          isError={trends.isError}
          isEmpty={!series.length}
        >
          <CostTrendChart data={series} />
        </DataState>
      </ChartCard>

      {/* Most expensive queries — filter bar + load-more */}
      <div ref={expensiveRef} className="space-y-3">
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          {/* Section header */}
          <div className="px-4 py-3 border-b border-slate-100">
            <h4 className="text-sm font-semibold text-slate-700">
              Most Expensive Queries
            </h4>
            <p className="text-xs text-slate-400 mt-0.5">
              Top queries by cost — expand row to see per-node breakdown
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
          <MostExpensiveTable
            rows={expensive.data?.queries ?? []}
            isLoading={expensive.isLoading}
            total={expensiveTotal}
            pageIndex={pageIndex}
            pageSize={pageSize}
            onPageChange={(p) => {
              setPageIndex(p.pageIndex);
              setPageSize(p.pageSize);
            }}
            search={filterValue.search ?? ""}
            sort={sort}
            order={order}
            onHeaderSort={handleSort}
          />
        </div>
      </div>
    </div>
  );
}
