"use client";

import { useState } from "react";

import {
  useAnalytics,
  type AnalyticsFilters,
  type DateRange,
} from "@/data/usage";

import {
  comma,
  money,
  nextSort,
  SortableTh,
  sortRows,
  TableShell,
  type SortState,
} from "./UsageBits";

/**
 * Usage by Model — every model that ran in the window, and what it cost.
 *
 * The service has no `model` dimension of its own: models are recorded on the
 * event's metadata, and `group_by` takes `metadata.*` for exactly this. The
 * provider comes back the same way, so the two are one query rather than a
 * table that guesses the vendor from the model's name.
 */

type ModelKey =
  "model" | "provider" | "requests" | "input" | "output" | "total" | "cost";

export default function ModelTable({
  range,
  filters,
  periodLabel,
}: {
  range: DateRange;
  filters: AnalyticsFilters | undefined;
  periodLabel: string;
}) {
  const [sort, setSort] = useState<SortState<ModelKey> | null>(null);

  const byModel = useAnalytics({
    date_range: range,
    group_by: ["metadata.model", "metadata.provider"],
    filters,
  });

  const rows = (byModel.data?.data ?? []).map((point) => ({
    model: point.dimensions["metadata.model"] || "—",
    provider: point.dimensions["metadata.provider"] || "—",
    requests: point.metrics.request_count ?? 0,
    input: point.metrics.input_tokens ?? 0,
    output: point.metrics.output_tokens ?? 0,
    total: point.metrics.total_tokens ?? 0,
    cost: point.metrics.cost?.total_cost_usd ?? 0,
  }));

  const sorted = sortRows(rows, sort);
  const totals = rows.reduce(
    (sum, row) => ({
      requests: sum.requests + row.requests,
      total: sum.total + row.total,
      cost: sum.cost + row.cost,
    }),
    { requests: 0, total: 0, cost: 0 },
  );
  const onSort = (key: ModelKey) =>
    setSort((current) => nextSort(current, key));

  return (
    <TableShell
      title="Usage by Model"
      note={`${periodLabel} · click a column heading to sort`}
      footer={
        <>
          <span>
            {rows.length} model{rows.length === 1 ? "" : "s"} · {periodLabel}
          </span>
          <span className="tabular-nums">
            {comma(totals.requests)} requests · {comma(totals.total)} tokens ·{" "}
            {money(totals.cost)}
          </span>
        </>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-line bg-sunken text-ink-2">
              <SortableTh
                columnKey="model"
                label="Model"
                sort={sort}
                onSort={onSort}
              />
              <SortableTh
                columnKey="provider"
                label="Provider"
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
                columnKey="input"
                label="Input tokens"
                sort={sort}
                onSort={onSort}
                numeric
              />
              <SortableTh
                columnKey="output"
                label="Output tokens"
                sort={sort}
                onSort={onSort}
                numeric
              />
              <SortableTh
                columnKey="total"
                label="Total tokens"
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
            {sorted.map((row) => (
              <tr
                key={`${row.model}-${row.provider}`}
                className="border-b border-line-soft last:border-0"
              >
                <td className="p-3 font-semibold text-ink">{row.model}</td>
                <td className="p-3 text-ink-2">{row.provider}</td>
                <td className="p-3 text-right text-ink-2 tabular-nums">
                  {comma(row.requests)}
                </td>
                <td className="p-3 text-right text-ink-2 tabular-nums">
                  {comma(row.input)}
                </td>
                <td className="p-3 text-right text-ink-2 tabular-nums">
                  {comma(row.output)}
                </td>
                <td className="p-3 text-right font-semibold text-ink tabular-nums">
                  {comma(row.total)}
                </td>
                <td className="p-3 text-right text-ink-2 tabular-nums">
                  {money(row.cost)}
                </td>
              </tr>
            ))}
            {!sorted.length && (
              <tr>
                <td colSpan={7} className="p-4 text-center text-ink-2">
                  {byModel.isLoading
                    ? "Reading…"
                    : "No model ran in this window."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </TableShell>
  );
}
