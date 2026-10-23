"use client";

import { useMemo, useState } from "react";
import dynamic from "next/dynamic";

import type { UseQueryResult } from "@tanstack/react-query";
import {
  Activity,
  ArrowDown,
  CheckCircle,
  Clock,
  DollarSign,
  Search,
  Users,
  Wallet,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { cn } from "@/platform/utils";

import { CHART_INITIAL_DIMENSION } from "../../constants";
import { useQueryLog } from "../../hooks/useQueryLog";
import { useTrends } from "../../hooks/useTrends";
import { deriveDailyTokens } from "../../lib/deriveDailyTokens";
import {
  deriveHourOfDay,
  derivePeak,
  type PeakView,
} from "../../lib/derivePeak";
import type { ScorecardDeltas } from "../../lib/scorecardDeltas";
import type { AnalyticsTabKey, AnalyticsWindow, Scorecard } from "../../types";
import ChartCard from "../ChartCard";
import DataState from "../DataState";
import KpiCard from "../KpiCard";
import KpiGrid from "../KpiGrid";
import RegressionBanner from "../RegressionBanner";

const QueryVolumeChart = dynamic(() => import("../charts/QueryVolumeChart"), {
  ssr: false,
});
const SuccessRateTrendChart = dynamic(
  () => import("../charts/SuccessRateTrendChart"),
  { ssr: false },
);
const CostTrendChart = dynamic(() => import("../charts/CostTrendChart"), {
  ssr: false,
});
const TokenTrendChart = dynamic(() => import("../charts/TokenTrendChart"), {
  ssr: false,
});

const fmtMs = (ms?: number) =>
  ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`;
const fmtPct = (p?: number) => (p == null ? "—" : `${p}%`);
const fmtInt = (n?: number | null) => (n == null ? "—" : n.toLocaleString());
const fmtUsd = (n?: number) => (n == null ? "—" : `$${n.toFixed(2)}`);
const fmtUsd3 = (n?: number) => (n == null ? "—" : `$${n.toFixed(3)}`);
const fmtTokens = (n?: number | null) => {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
};

type VolumeFilter = "all" | "answered" | "unanswered";

/** Small "View … →" link that jumps to another analytics tab. */
function JumpLink({
  label,
  tab,
  onTabChange,
}: {
  label: string;
  tab: AnalyticsTabKey;
  onTabChange?: (tab: AnalyticsTabKey) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onTabChange?.(tab)}
      disabled={!onTabChange}
      className={cn(
        "inline-flex items-center gap-1 text-xs font-medium text-indigo-600 transition-colors",
        onTabChange ? "hover:text-indigo-700" : "cursor-default opacity-60",
      )}
    >
      {label}
      <ArrowDown className="size-3" />
    </button>
  );
}

/** Labelled token pill (Input / Output) used in the cost cards. */
function TokenPill({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "amber" | "emerald";
}) {
  return (
    <div
      className={cn(
        "flex-1 rounded-md px-2 py-1.5 text-center",
        tone === "amber" ? "bg-amber-50" : "bg-emerald-50",
      )}
    >
      <div
        className={cn(
          "text-[10px] font-medium uppercase tracking-wide",
          tone === "amber" ? "text-amber-700" : "text-emerald-700",
        )}
      >
        {label}
      </div>
      <div className="text-sm font-semibold text-slate-900">{value}</div>
    </div>
  );
}

export default function OverviewTab({
  scorecard,
  window,
  agentId,
  onTabChange,
  deltas,
}: {
  scorecard: UseQueryResult<Scorecard>;
  window: AnalyticsWindow;
  agentId?: string | null;
  onTabChange?: (tab: AnalyticsTabKey) => void;
  deltas?: ScorecardDeltas;
}) {
  const sc = scorecard.data;
  const [peakView, setPeakView] = useState<PeakView>("day");
  const [volumeFilter, setVolumeFilter] = useState<VolumeFilter>("all");

  const trends = useTrends(agentId, window, {
    bucket: "day",
    metrics: ["success_rate", "avg_ms", "cost", "total_queries"],
  });
  const series = useMemo(
    () => trends.data?.series ?? [],
    [trends.data?.series],
  );

  // All KPI delta pills use the client-computed % change vs the previous period
  // (see page.tsx) so they render even when the API omits `trend`.
  const avgDirection = deltas?.avg_ms?.direction ?? "flat";

  // Avg tokens / query — aggregate totals ÷ query count (no daily source).
  const totalQueries = sc?.usage?.total_queries ?? 0;
  const avgInput =
    sc?.cost?.input_tokens && totalQueries
      ? Math.round(sc.cost.input_tokens / totalQueries).toLocaleString()
      : "—";
  const avgOutput =
    sc?.cost?.output_tokens && totalQueries
      ? Math.round(sc.cost.output_tokens / totalQueries).toLocaleString()
      : "—";

  // Query Volume: "all" uses the raw daily counts; answered / unanswered are
  // derived per bucket from total_queries × success_rate (same split QualityTab
  // uses), since usage.per_day has no answered/unanswered breakdown.
  const volumeData = useMemo(() => {
    if (volumeFilter === "all") return sc?.usage.per_day ?? [];
    return series.map((s) => {
      const total = s.total_queries ?? 0;
      const answered = Math.round((total * (s.success_rate ?? 0)) / 100);
      return {
        day: s.bucket.slice(0, 10),
        queries:
          volumeFilter === "answered"
            ? answered
            : Math.max(0, total - answered),
      };
    });
  }, [volumeFilter, sc?.usage.per_day, series]);

  // Peak usage: day-of-week and month-of-year derive from usage.per_day.
  // Hour-of-day has no source in daily data, so we derive it from a sample of
  // recent query timestamps (the API caps `limit` at 100) — an approximation
  // over the latest activity, since there is no hourly rollup endpoint.
  const hourSample = useQueryLog(agentId, window, {
    sort: "created_at",
    order: "desc",
    limit: 100,
  });

  // Daily input/output tokens rolled up from the recent-query sample — the
  // trends endpoint exposes no per-bucket token metric, so this mirrors the
  // hour-of-day approximation (capped at the API's 100-row sample).
  const tokenSeries = useMemo(
    () => deriveDailyTokens(hourSample.data?.queries),
    [hourSample.data?.queries],
  );

  // Month-of-Year is only meaningful once the window spans 2+ calendar months.
  const monthsAvailable = useMemo(
    () => new Set((sc?.usage.per_day ?? []).map((d) => d.day.slice(0, 7))).size,
    [sc?.usage.per_day],
  );
  const monthDisabled = monthsAvailable < 2;

  const peakBuckets = useMemo(() => {
    if (peakView === "hour") {
      return deriveHourOfDay(
        hourSample.data?.queries?.map((q) => q.timestamp) ?? [],
      );
    }
    return derivePeak(sc?.usage.per_day ?? [], peakView);
  }, [peakView, hourSample.data?.queries, sc?.usage.per_day]);

  const peakLabels = useMemo(() => {
    if (!peakBuckets) return [];
    const max = Math.max(...peakBuckets.map((b) => b.value));
    return peakBuckets
      .filter((b) => b.value === max && max > 0)
      .map((b) => b.label);
  }, [peakBuckets]);

  return (
    <div className="space-y-4">
      <RegressionBanner regression={trends.data?.regression} />

      <KpiGrid>
        <KpiCard
          label="Total Queries"
          value={fmtInt(sc?.usage.total_queries)}
          valueSize="lg"
          delta={deltas?.total_queries?.abs ?? null}
          direction={deltas?.total_queries?.direction ?? "flat"}
          deltaVariant="pill"
          deltaSuffix="%"
          deltaText="vs prev period"
          goodWhen="up"
          icon={<Search className="size-4 text-indigo-500" />}
          iconBg="bg-indigo-50"
          isLoading={scorecard.isLoading}
          footer={
            <JumpLink
              label="View all queries"
              tab="querylogs"
              onTabChange={onTabChange}
            />
          }
        >
          <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs">
            <span className="inline-flex items-center gap-1 text-slate-500">
              <Users className="size-3.5" />
              Active Users
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="font-semibold text-slate-700">
                {fmtInt(sc?.usage.unique_users)}
              </span>
              {deltas?.unique_users &&
                deltas.unique_users.direction !== "flat" && (
                  <span
                    className={cn(
                      "inline-flex items-center gap-0.5 font-medium",
                      deltas.unique_users.direction === "up"
                        ? "text-emerald-600"
                        : "text-red-600",
                    )}
                  >
                    {deltas.unique_users.direction === "up" ? "↑" : "↓"}
                    {deltas.unique_users.abs}%
                  </span>
                )}
            </span>
          </div>
        </KpiCard>

        <KpiCard
          label="Success Rate"
          value={fmtPct(sc?.quality.success_rate)}
          valueSize="lg"
          delta={deltas?.success_rate?.abs ?? null}
          direction={deltas?.success_rate?.direction ?? "flat"}
          deltaVariant="pill"
          deltaSuffix="%"
          deltaText="vs prev period"
          goodWhen="up"
          icon={<CheckCircle className="size-4 text-emerald-500" />}
          iconBg="bg-emerald-50"
          valueColor={
            sc?.quality.success_rate != null ? "text-emerald-600" : undefined
          }
          isLoading={scorecard.isLoading}
          footer={
            <JumpLink
              label="View answered"
              tab="quality"
              onTabChange={onTabChange}
            />
          }
        />

        <KpiCard
          label="Avg Response Time"
          value={fmtMs(sc?.perf.avg_ms)}
          valueSize="lg"
          delta={deltas?.avg_ms?.abs ?? null}
          direction={avgDirection}
          deltaVariant="pill"
          deltaSuffix="%"
          deltaText={
            avgDirection === "up" ? "slower vs prev" : "faster vs prev"
          }
          goodWhen="down"
          icon={<Clock className="size-4 text-amber-500" />}
          iconBg="bg-amber-50"
          isLoading={scorecard.isLoading}
          footer={
            <JumpLink
              label="View slowest"
              tab="performance"
              onTabChange={onTabChange}
            />
          }
        />

        <KpiCard
          label="Avg Cost / Query"
          value={fmtUsd3(sc?.cost?.avg_cost_per_query)}
          valueSize="lg"
          delta={deltas?.avg_cost_per_query?.abs ?? null}
          direction={deltas?.avg_cost_per_query?.direction ?? "flat"}
          deltaVariant="pill"
          deltaSuffix="%"
          deltaText="vs prev period"
          goodWhen="down"
          icon={<DollarSign className="size-4 text-indigo-500" />}
          iconBg="bg-indigo-50"
          isLoading={scorecard.isLoading}
          footer={
            <JumpLink
              label="View expensive"
              tab="cost"
              onTabChange={onTabChange}
            />
          }
        >
          <div>
            <div className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Avg Tokens / Query
            </div>
            <div className="mt-1 flex gap-2">
              <TokenPill label="Input" value={avgInput} tone="amber" />
              <TokenPill label="Output" value={avgOutput} tone="emerald" />
            </div>
          </div>
        </KpiCard>

        <KpiCard
          label="Total Cost"
          value={fmtUsd(sc?.cost?.total_cost)}
          valueSize="lg"
          delta={deltas?.total_cost?.abs ?? null}
          direction={deltas?.total_cost?.direction ?? "flat"}
          deltaVariant="pill"
          deltaSuffix="%"
          deltaText="vs prev period"
          goodWhen="down"
          icon={<Wallet className="size-4 text-indigo-500" />}
          iconBg="bg-indigo-50"
          isLoading={scorecard.isLoading}
          footer={
            <JumpLink
              label="View expensive"
              tab="cost"
              onTabChange={onTabChange}
            />
          }
        >
          <div>
            <div className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Total Tokens · {fmtTokens(sc?.cost?.total_tokens)}
            </div>
            <div className="mt-1 flex gap-2">
              <TokenPill
                label="Input"
                value={fmtTokens(sc?.cost?.input_tokens)}
                tone="amber"
              />
              <TokenPill
                label="Output"
                value={fmtTokens(sc?.cost?.output_tokens)}
                tone="emerald"
              />
            </div>
          </div>
        </KpiCard>
      </KpiGrid>

      <div className="flex gap-4 max-md:flex-col">
        <ChartCard
          title="Query Volume"
          subtitle="Daily queries over selected period"
          className="h-[300px]"
          action={
            <select
              aria-label="Filter query volume"
              value={volumeFilter}
              onChange={(e) => setVolumeFilter(e.target.value as VolumeFilter)}
              className="h-8 cursor-pointer appearance-none rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 outline-none transition-colors hover:bg-slate-50 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-500/20"
            >
              <option value="all">All Queries</option>
              <option value="answered">Answered</option>
              <option value="unanswered">Unanswered</option>
            </select>
          }
        >
          <DataState
            isLoading={scorecard.isLoading}
            isError={scorecard.isError}
            isEmpty={!volumeData.length}
          >
            <QueryVolumeChart data={volumeData} />
          </DataState>
        </ChartCard>
        <ChartCard
          title="Success Rate Trend"
          subtitle="Current vs previous period"
          className="h-[300px]"
        >
          <DataState
            isLoading={trends.isLoading}
            isError={trends.isError}
            isEmpty={!series.length}
          >
            <SuccessRateTrendChart data={series} />
          </DataState>
        </ChartCard>
      </div>

      <div className="flex gap-4 max-md:flex-col">
        <ChartCard
          title="Cost Trend"
          subtitle="Daily total cost and avg cost per query"
          className="h-[300px]"
        >
          <DataState
            isLoading={trends.isLoading}
            isError={trends.isError}
            isEmpty={!series.some((s) => s.cost != null)}
          >
            <CostTrendChart data={series} />
          </DataState>
        </ChartCard>
        <ChartCard
          title="Token Consumption Trend"
          subtitle="Daily input + output tokens"
          className="h-[300px]"
        >
          <DataState
            isLoading={hourSample.isLoading}
            isError={hourSample.isError}
            isEmpty={!tokenSeries.length}
          >
            <TokenTrendChart data={tokenSeries} />
          </DataState>
        </ChartCard>
      </div>

      {/* Peak Usage Periods */}
      <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4">
        <div className="flex items-start justify-between mb-1">
          <div>
            <h4 className="text-sm font-semibold text-slate-700">
              Peak Usage Periods
            </h4>
            <p className="text-xs text-slate-400 mt-0.5">
              When is this agent most used?
            </p>
          </div>
          <div className="flex items-center gap-1 text-xs">
            {(["hour", "day", "month"] as PeakView[]).map((v) => {
              const disabled = v === "month" && monthDisabled;
              return (
                <button
                  key={v}
                  type="button"
                  disabled={disabled}
                  title={disabled ? "Needs data spanning 2+ months" : undefined}
                  onClick={() => setPeakView(v)}
                  className={cn(
                    "px-2 py-1 rounded transition-colors",
                    disabled
                      ? "cursor-not-allowed text-slate-300"
                      : peakView === v
                        ? "text-indigo-600 font-semibold"
                        : "text-slate-400 hover:text-slate-600",
                  )}
                >
                  {v === "hour"
                    ? "Hour of Day"
                    : v === "day"
                      ? "Day of Week"
                      : "Month of Year"}
                </button>
              );
            })}
          </div>
        </div>

        {peakBuckets ? (
          <>
            <div className="h-[180px] mt-2">
              <ResponsiveContainer
                width="100%"
                height="100%"
                initialDimension={CHART_INITIAL_DIMENSION}
              >
                <BarChart
                  data={peakBuckets}
                  margin={{ top: 4, right: 8, left: 0, bottom: 4 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#0000000D" />
                  <XAxis
                    dataKey="label"
                    fontSize={12}
                    tickLine={false}
                    axisLine={false}
                    stroke="#666"
                  />
                  <YAxis
                    fontSize={12}
                    tickLine={false}
                    axisLine={false}
                    allowDecimals={false}
                    stroke="#666"
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "white",
                      border: "1px solid #e5e7eb",
                      borderRadius: "6px",
                      padding: "8px 12px",
                    }}
                  />
                  <Bar dataKey="value" fill="#6366f1" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            {peakLabels.length > 0 && (
              <p className="text-xs text-slate-400 mt-2">
                <Activity className="size-3 inline mr-1" />
                {peakView === "month"
                  ? "Peak months"
                  : peakView === "hour"
                    ? "Peak hours"
                    : "Peak days"}
                : {peakLabels.join(" & ")}
              </p>
            )}
          </>
        ) : (
          <div className="h-[180px] flex items-center justify-center text-sm text-slate-400">
            {peakView === "hour" && hourSample.isLoading
              ? "Loading…"
              : "No usage data for this period."}
          </div>
        )}
      </div>
    </div>
  );
}
