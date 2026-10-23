"use client";

import { Network } from "lucide-react";

import { cn } from "@/platform/utils";

import type { NodeStat } from "../lib/deriveNodeStats";

const HEADERS = [
  "Node / Service",
  "Total Calls",
  "Frequency %",
  "Answered",
  "Unanswered",
  "Answer Rate",
  "Avg Latency",
  "P50",
  "P95",
  "Avg Cost",
  "% of Cost",
  "Avg Input Tokens",
  "Avg Output Tokens",
];

const fmtMs = (ms: number | null) =>
  ms == null
    ? "—"
    : ms < 1000
      ? `${Math.round(ms)}ms`
      : `${(ms / 1000).toFixed(1)}s`;
const fmtInt = (n: number | null) =>
  n == null ? "—" : Math.round(n).toLocaleString();

const answerRateColor = (rate: number | null) =>
  rate == null
    ? "text-slate-400"
    : rate >= 95
      ? "text-emerald-600"
      : rate >= 85
        ? "text-amber-600"
        : "text-red-600";

const answerRateBar = (rate: number | null) =>
  rate == null
    ? "bg-slate-300"
    : rate >= 95
      ? "bg-emerald-500"
      : rate >= 85
        ? "bg-amber-500"
        : "bg-red-500";

const latencyColor = (ms: number | null) =>
  ms == null
    ? "text-slate-400"
    : ms < 700
      ? "text-emerald-600"
      : ms < 2000
        ? "text-amber-600"
        : "text-red-600";

const costPctColor = (pct: number) =>
  pct > 50 ? "text-red-600" : pct > 20 ? "text-amber-600" : "text-slate-500";

export interface NodeBreakdownTableProps {
  stats: NodeStat[];
  /** Number of queries in the sample the stats were derived from. */
  sampleSize: number;
  isLoading?: boolean;
  isError?: boolean;
  /** "latency" (default) keeps deriveNodeStats order; "cost" sorts by total cost desc. */
  sortBy?: "latency" | "cost";
}

/**
 * Unified per-node / per-service breakdown — the same 13-column table is
 * embedded in the Quality, Performance and Cost tabs. All figures are a
 * client-side aggregation over a recent-query sample (no rollup endpoint).
 */
export default function NodeBreakdownTable({
  stats,
  sampleSize,
  isLoading,
  isError,
  sortBy = "latency",
}: NodeBreakdownTableProps) {
  const rows =
    sortBy === "cost" ? [...stats].sort((a, b) => b.cost - a.cost) : stats;

  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h4 className="text-sm font-semibold text-slate-700">
            Per-Node / Service Breakdown
          </h4>
          <p className="text-xs text-slate-400 mt-0.5">
            Quality, latency, cost and token metrics per processing stage
          </p>
        </div>
        {sampleSize > 0 && (
          <span className="text-xs text-slate-400">
            Based on the most recent {sampleSize} queries
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[1100px] text-xs">
          <thead>
            <tr className="bg-slate-50 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              {HEADERS.map((h, i) => (
                <th
                  key={h}
                  className={cn(
                    "px-3 py-2 whitespace-nowrap border-b border-slate-100",
                    i === 0 ? "text-left" : "text-center",
                  )}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <tr key={i}>
                  {HEADERS.map((h) => (
                    <td key={h} className="px-3 py-2.5">
                      <div className="h-4 rounded bg-slate-100 animate-pulse" />
                    </td>
                  ))}
                </tr>
              ))
            ) : isError ? (
              <tr>
                <td
                  colSpan={HEADERS.length}
                  className="px-4 py-10 text-center text-sm text-red-500"
                >
                  Failed to load per-node data.
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td
                  colSpan={HEADERS.length}
                  className="px-4 py-10 text-center text-sm text-slate-400"
                >
                  <Network className="size-8 mx-auto mb-2 text-slate-300" />
                  Per-node data not available for these queries.
                </td>
              </tr>
            ) : (
              rows.map((n) => (
                <tr
                  key={n.node}
                  className="border-b border-slate-50 last:border-0 hover:bg-slate-50/60"
                >
                  <td className="px-3 py-2.5">
                    <span
                      data-testid="node-name"
                      className="font-mono text-[11px] font-bold text-slate-700"
                    >
                      {n.node}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-center font-semibold tabular-nums">
                    {n.calls.toLocaleString()}
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] font-bold text-indigo-600 tabular-nums">
                      {n.freq_pct.toFixed(1)}%
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-center font-semibold text-emerald-700 tabular-nums">
                    {n.answered.toLocaleString()}
                  </td>
                  <td
                    className={cn(
                      "px-3 py-2.5 text-center font-semibold tabular-nums",
                      n.unanswered > 0 ? "text-red-600" : "text-slate-400",
                    )}
                  >
                    {n.unanswered.toLocaleString()}
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    <span
                      className={cn(
                        "text-[13px] font-bold tabular-nums",
                        answerRateColor(n.answer_rate),
                      )}
                    >
                      {n.answer_rate == null
                        ? "—"
                        : `${n.answer_rate.toFixed(1)}%`}
                    </span>
                    {n.answer_rate != null && (
                      <div className="mx-auto mt-1 h-1 w-14 rounded-full bg-slate-100">
                        <div
                          className={cn(
                            "h-full rounded-full",
                            answerRateBar(n.answer_rate),
                          )}
                          style={{ width: `${Math.min(100, n.answer_rate)}%` }}
                        />
                      </div>
                    )}
                  </td>
                  <td
                    className={cn(
                      "px-3 py-2.5 text-center font-semibold tabular-nums",
                      latencyColor(n.avg_ms),
                    )}
                  >
                    {fmtMs(n.avg_ms)}
                  </td>
                  <td className="px-3 py-2.5 text-center text-slate-500 tabular-nums">
                    {fmtMs(n.p50_ms)}
                  </td>
                  <td
                    className={cn(
                      "px-3 py-2.5 text-center tabular-nums",
                      (n.p95_ms ?? 0) > 5000
                        ? "text-red-600"
                        : "text-slate-500",
                    )}
                  >
                    {fmtMs(n.p95_ms)}
                  </td>
                  <td className="px-3 py-2.5 text-center font-semibold tabular-nums">
                    {n.avg_cost == null ? "—" : `$${n.avg_cost.toFixed(5)}`}
                  </td>
                  <td
                    className={cn(
                      "px-3 py-2.5 text-center font-semibold tabular-nums",
                      costPctColor(n.cost_pct),
                    )}
                  >
                    {n.cost_pct.toFixed(1)}%
                  </td>
                  <td className="px-3 py-2.5 text-center text-slate-500 tabular-nums">
                    {fmtInt(n.avg_in)}
                  </td>
                  <td className="px-3 py-2.5 text-center text-slate-500 tabular-nums">
                    {fmtInt(n.avg_out)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-2.5 flex flex-wrap gap-x-5 gap-y-1 border-t border-slate-100 pt-2.5 text-[11px] text-slate-400">
        <span>
          <strong className="text-slate-600">Frequency %</strong> = node queries
          ÷ sample queries
        </span>
        <span>
          <strong className="text-slate-600">Answer Rate</strong> = answered ÷
          node queries
        </span>
        <span>
          <strong className="text-slate-600">% of Cost</strong> = node cost ÷
          sample node cost
        </span>
        <span>
          Latency: <span className="font-bold text-emerald-500">■</span>{" "}
          &lt;700ms <span className="font-bold text-amber-500">■</span> &lt;2s{" "}
          <span className="font-bold text-red-500">■</span> &gt;2s
        </span>
      </div>
    </div>
  );
}
