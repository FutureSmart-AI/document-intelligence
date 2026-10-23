"use client";

import { useState } from "react";
import dynamic from "next/dynamic";

import type { UseQueryResult } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle,
  Minus,
  ThumbsUp,
  XCircle,
} from "lucide-react";
import { Cell, Pie, PieChart, Tooltip } from "recharts";

import { useCardJump } from "../../hooks/useCardJump";
import { useQueryLog } from "../../hooks/useQueryLog";
import { useTrends } from "../../hooks/useTrends";
import { deriveFeedbackTrend } from "../../lib/deriveFeedbackTrend";
import { deriveNodeStats } from "../../lib/deriveNodeStats";
import type { ScorecardDeltas } from "../../lib/scorecardDeltas";
import type { AnalyticsWindow, Scorecard, TrendsResponse } from "../../types";
import ChartCard from "../ChartCard";
import DataState from "../DataState";
import KpiCard from "../KpiCard";
import NodeBreakdownTable from "../NodeBreakdownTable";
import type { QueryFilterValue } from "../QueryFilterBar";
import RegressionBanner from "../RegressionBanner";
import QualityQueryTable from "../tables/QualityQueryTable";

const AnsweredVsUnansweredChart = dynamic(
  () => import("../charts/AnsweredVsUnansweredChart"),
  { ssr: false },
);
const UserFeedbackChart = dynamic(() => import("../charts/UserFeedbackChart"), {
  ssr: false,
});

const fmtInt = (n?: number | null) => (n == null ? "—" : n.toLocaleString());
const fmtPct = (p?: number | null) => (p == null ? "—" : `${p.toFixed(1)}%`);

export function deriveAnsweredSplit(
  series: TrendsResponse["series"],
): { bucket: string; answered: number; unanswered: number }[] {
  return series.map((s) => {
    const total = s.total_queries ?? 0;
    const answered = Math.round((total * (s.success_rate ?? 0)) / 100);
    return {
      bucket: s.bucket.slice(0, 10),
      answered,
      unanswered: Math.max(0, total - answered),
    };
  });
}

function derivePctSplit(series: TrendsResponse["series"]) {
  return series.map((s) => ({
    bucket: s.bucket.slice(0, 10),
    answered: s.success_rate ?? 0,
    unanswered: Math.max(0, 100 - (s.success_rate ?? 0)),
  }));
}

const cardBtnCls =
  "h-full w-full text-left rounded-lg border border-gray-200 bg-white shadow-sm p-4 relative transition-all hover:ring-2 focus-visible:outline-none focus-visible:ring-2";

export default function QualityTab({
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
  const trends = useTrends(agentId, window, {
    bucket: "day",
    metrics: ["success_rate", "total_queries"],
  });
  const pctSplitData = derivePctSplit(trends.data?.series ?? []);

  // Recent-100 sample (API caps `limit` at 100) — powers the per-node
  // breakdown and the feedback-over-time chart client-side; there is no
  // per-node rollup or daily feedback series endpoint.
  const sample = useQueryLog(agentId, window, {
    sort: "created_at",
    order: "desc",
    limit: 100,
  });
  const sampleRows = sample.data?.queries ?? [];
  const nodeStats = deriveNodeStats(sampleRows);
  const feedbackTrend = deriveFeedbackTrend(sampleRows);

  const total = sc ? sc.quality.answered + sc.quality.unanswered : 0;
  const successRate = sc?.quality.success_rate ?? null;
  // Answered/unanswered share the success-rate % change vs the previous period
  // (computed in page.tsx), so the pills render even when the API omits `trend`.
  const answeredDelta = deltas?.success_rate?.abs ?? null;
  const answeredDirection = deltas?.success_rate?.direction ?? "flat";

  const fb = sc?.quality.feedback ?? null;
  const positiveCount = fb?.positive ?? null;
  const negativeCount = fb?.negative ?? null;
  const feedbackTotal = fb?.total ?? null;
  const noneCount = sc ? Math.max(0, total - (feedbackTotal ?? 0)) : null;
  const positiveRate =
    fb && fb.total > 0 ? (fb.positive / fb.total) * 100 : null;
  // Backend does not yet return a positive-rate trend delta.
  const positiveRateDelta: number | null = null;
  const positiveRateDirection: "up" | "down" | "flat" = "flat";

  const donutData = sc
    ? [
        { name: "Answered", value: sc.quality.answered, color: "#10b981" },
        { name: "Unanswered", value: sc.quality.unanswered, color: "#ef4444" },
      ]
    : [];

  // Query table filter state + card-jump
  const [qf, setQf] = useState<QueryFilterValue>({});
  const { ref, jump } = useCardJump();

  function handleCardClick(next: QueryFilterValue) {
    setQf(next);
    jump();
  }

  return (
    <div className="space-y-4">
      <RegressionBanner regression={trends.data?.regression} />

      {/* KPI cards row + donut */}
      <div className="grid grid-cols-4 gap-4 max-md:grid-cols-1">
        <button
          type="button"
          aria-label="Answered Queries"
          className={`${cardBtnCls} hover:ring-emerald-400 focus-visible:ring-emerald-400`}
          onClick={() => handleCardClick({ outcome: "success" })}
        >
          <KpiCard
            label="Answered Queries"
            value={fmtInt(sc?.quality.answered)}
            delta={answeredDelta}
            direction={answeredDirection}
            deltaText="vs prev period"
            deltaSuffix="%"
            goodWhen="up"
            icon={<CheckCircle className="size-4 text-emerald-500" />}
            iconBg="bg-emerald-50"
            valueColor="text-emerald-500"
            hint={
              sc
                ? `${fmtPct(successRate)} of ${fmtInt(total)} queries`
                : undefined
            }
            isLoading={scorecard.isLoading}
          />
        </button>
        <button
          type="button"
          aria-label="Unanswered Queries"
          className={`${cardBtnCls} hover:ring-red-400 focus-visible:ring-red-400`}
          onClick={() => handleCardClick({ outcome: "failure" })}
        >
          <KpiCard
            label="Unanswered Queries"
            value={fmtInt(sc?.quality.unanswered)}
            delta={answeredDelta != null ? Math.abs(answeredDelta) : null}
            direction={
              answeredDirection === "up"
                ? "down"
                : answeredDirection === "down"
                  ? "up"
                  : "flat"
            }
            deltaText="vs prev period"
            deltaSuffix="%"
            goodWhen="down"
            icon={<XCircle className="size-4 text-red-500" />}
            iconBg="bg-red-50"
            valueColor="text-red-500"
            hint={
              sc
                ? `${fmtPct(100 - (successRate ?? 0))} of ${fmtInt(total)} queries`
                : undefined
            }
            isLoading={scorecard.isLoading}
          />
        </button>

        {/* User Feedback card */}
        <div className="h-full rounded-lg border border-gray-200 bg-white shadow-sm p-4 relative">
          <div className="absolute top-4 right-4 size-8 rounded-lg flex items-center justify-center bg-amber-50">
            <ThumbsUp className="size-4 text-amber-500" />
          </div>
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
            User Feedback
          </div>
          {scorecard.isLoading ? (
            <div className="mt-2 h-7 w-24 rounded bg-slate-100 animate-pulse" />
          ) : (
            <>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  className="flex-1 rounded-md bg-emerald-50 p-2 text-left transition-opacity hover:opacity-75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  onClick={() => handleCardClick({ feedback: "pos" })}
                >
                  <span className="text-xl font-bold text-emerald-700">
                    {fmtInt(positiveCount)}
                  </span>
                  <p className="text-[11px] text-emerald-700">👍 Positive</p>
                  <p className="mt-1 text-[10px] font-bold text-emerald-700">
                    View ↓
                  </p>
                </button>
                <button
                  type="button"
                  className="flex-1 rounded-md bg-red-50 p-2 text-left transition-opacity hover:opacity-75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
                  onClick={() => handleCardClick({ feedback: "neg" })}
                >
                  <span className="text-xl font-bold text-red-700">
                    {fmtInt(negativeCount)}
                  </span>
                  <p className="text-[11px] text-red-700">👎 Negative</p>
                  <p className="mt-1 text-[10px] font-bold text-red-700">
                    View ↓
                  </p>
                </button>
              </div>
              {positiveRate != null && (
                <p className="mt-2 text-[11px] text-slate-400">
                  {positiveRate.toFixed(1)}% positive
                  {noneCount != null && ` · ${fmtInt(noneCount)} no feedback`}
                </p>
              )}
              {positiveRateDelta != null &&
                positiveRateDirection !== "flat" && (
                  <div
                    className={`mt-2 inline-flex items-center gap-1 text-xs font-medium ${
                      positiveRateDirection === "up"
                        ? "text-emerald-600"
                        : "text-red-600"
                    }`}
                  >
                    {positiveRateDirection === "up" ? (
                      <ArrowUp className="size-3" />
                    ) : (
                      <ArrowDown className="size-3" />
                    )}
                    <span>{Math.abs(positiveRateDelta)}%</span>
                    <span className="text-slate-400 font-normal">
                      positive rate vs prev
                    </span>
                  </div>
                )}
            </>
          )}
        </div>

        {/* Donut card */}
        <div className="h-full rounded-lg border border-gray-200 bg-white shadow-sm p-4 flex items-center gap-3">
          {scorecard.isLoading ? (
            <div className="size-20 rounded-full bg-slate-100 animate-pulse mx-auto" />
          ) : donutData.length ? (
            <>
              <div
                className="relative shrink-0"
                style={{ width: 110, height: 110 }}
              >
                <PieChart width={110} height={110}>
                  <Pie
                    data={donutData}
                    cx="50%"
                    cy="50%"
                    innerRadius={38}
                    outerRadius={52}
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
                  <span className="text-xl font-bold text-slate-900">
                    {Math.round(successRate ?? 0)}%
                  </span>
                </div>
              </div>
              <div className="space-y-1.5 min-w-0">
                <div className="flex items-center gap-1.5">
                  <div className="size-2 rounded-full bg-emerald-500 shrink-0" />
                  <span className="text-xs text-slate-600">
                    {fmtPct(successRate)} Answered
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <div className="size-2 rounded-full bg-red-500 shrink-0" />
                  <span className="text-xs text-slate-600">
                    {fmtPct(100 - (successRate ?? 0))} Unanswered
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 mt-1">
                  Total: {fmtInt(total)} queries
                </p>
              </div>
            </>
          ) : (
            <div className="w-full flex items-center justify-center text-xs text-slate-400">
              No data
            </div>
          )}
        </div>
      </div>

      {/* Per-node / service quality breakdown — above the charts */}
      <NodeBreakdownTable
        stats={nodeStats}
        sampleSize={sampleRows.length}
        isLoading={sample.isLoading}
        isError={sample.isError}
      />

      {/* Trend charts */}
      <div className="flex gap-4 max-md:flex-col">
        <ChartCard
          title="Answered vs Unanswered Trend"
          subtitle="Daily breakdown — current vs previous period (dashed)"
          className="h-[320px]"
        >
          <DataState
            isLoading={trends.isLoading}
            isError={trends.isError}
            isEmpty={!pctSplitData.length}
          >
            <AnsweredVsUnansweredChart data={pctSplitData} />
          </DataState>
        </ChartCard>
        <ChartCard
          title="User Feedback Over Time"
          subtitle="Positive and negative feedback by day"
          className="h-[320px]"
        >
          <p className="text-[11px] text-slate-400 mb-1">
            Based on the most recent 100 queries
          </p>
          <DataState
            isLoading={sample.isLoading}
            isError={sample.isError}
            isEmpty={!feedbackTrend.length}
          >
            <UserFeedbackChart data={feedbackTrend} />
          </DataState>
        </ChartCard>
      </div>

      {/* Feedback breakdown stat cards */}
      <div className="grid grid-cols-4 gap-4 max-md:grid-cols-2">
        {/* Positive */}
        <button
          type="button"
          className={`${cardBtnCls} hover:ring-emerald-400 focus-visible:ring-emerald-400`}
          onClick={() => handleCardClick({ feedback: "pos" })}
        >
          <div className="text-2xl font-semibold text-emerald-500">
            {scorecard.isLoading ? (
              <div className="h-7 w-12 rounded bg-slate-100 animate-pulse" />
            ) : (
              fmtInt(positiveCount)
            )}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">👍 Positive</div>
          {positiveRateDelta != null && positiveRateDirection !== "flat" && (
            <div
              className={`mt-2 inline-flex items-center gap-1 text-xs font-medium ${
                positiveRateDirection === "up"
                  ? "text-emerald-600"
                  : "text-red-600"
              }`}
            >
              {positiveRateDirection === "up" ? (
                <ArrowUp className="size-3" />
              ) : (
                <ArrowDown className="size-3" />
              )}
              <span>{Math.abs(positiveRateDelta)}%</span>
              <span className="text-slate-400 font-normal">vs prev</span>
            </div>
          )}
        </button>

        {/* Negative */}
        <button
          type="button"
          className={`${cardBtnCls} hover:ring-red-400 focus-visible:ring-red-400`}
          onClick={() => handleCardClick({ feedback: "neg" })}
        >
          <div className="text-2xl font-semibold text-red-500">
            {scorecard.isLoading ? (
              <div className="h-7 w-12 rounded bg-slate-100 animate-pulse" />
            ) : (
              fmtInt(negativeCount)
            )}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">👎 Negative</div>
          {positiveRateDelta != null && positiveRateDirection !== "flat" && (
            <div
              className={`mt-2 inline-flex items-center gap-1 text-xs font-medium ${
                positiveRateDirection === "up"
                  ? "text-emerald-600"
                  : "text-red-600"
              }`}
            >
              {positiveRateDirection === "up" ? (
                <ArrowDown className="size-3" />
              ) : (
                <ArrowUp className="size-3" />
              )}
              <span>{Math.abs(positiveRateDelta)}%</span>
              <span className="text-slate-400 font-normal">vs prev</span>
            </div>
          )}
        </button>

        {/* No feedback */}
        <div className="h-full rounded-lg border border-gray-200 bg-white shadow-sm p-4">
          <div className="text-2xl font-semibold text-slate-700">
            {scorecard.isLoading ? (
              <div className="h-7 w-12 rounded bg-slate-100 animate-pulse" />
            ) : (
              fmtInt(noneCount)
            )}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">No feedback</div>
          <div className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-slate-400">
            <Minus className="size-3" />
            <span>same</span>
          </div>
        </div>

        {/* Positive rate */}
        <div className="h-full rounded-lg border border-gray-200 bg-white shadow-sm p-4">
          <div className="text-2xl font-semibold text-blue-600">
            {scorecard.isLoading ? (
              <div className="h-7 w-16 rounded bg-slate-100 animate-pulse" />
            ) : positiveRate != null ? (
              `${positiveRate.toFixed(1)}%`
            ) : (
              "—"
            )}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">Positive rate</div>
          {positiveRateDelta != null && positiveRateDirection !== "flat" && (
            <div
              className={`mt-2 inline-flex items-center gap-1 text-xs font-medium ${
                positiveRateDirection === "up"
                  ? "text-emerald-600"
                  : "text-red-600"
              }`}
            >
              {positiveRateDirection === "up" ? (
                <ArrowUp className="size-3" />
              ) : (
                <ArrowDown className="size-3" />
              )}
              <span>{Math.abs(positiveRateDelta)}%</span>
              <span className="text-slate-400 font-normal">vs prev</span>
            </div>
          )}
        </div>
      </div>

      {/* Query table */}
      <QualityQueryTable
        agentId={agentId}
        window={window}
        filterValue={qf}
        onFilterChange={setQf}
        tableRef={ref}
      />
    </div>
  );
}
