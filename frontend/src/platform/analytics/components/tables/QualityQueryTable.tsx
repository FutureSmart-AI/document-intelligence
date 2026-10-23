"use client";

import { Fragment, useEffect, useState } from "react";
import type React from "react";

import dayjs from "dayjs";
import { ChevronRight, ChevronsUpDown } from "lucide-react";

import { TablePagination } from "@/platform/components/ui";
import { cn } from "@/platform/utils";

import { useAnalyticsScope } from "../../AnalyticsScope";
import { useQueryLog } from "../../hooks/useQueryLog";
import { feedbackEmoji } from "../../lib/normalizeFeedback";
import { rowMatchesSearch } from "../../lib/rowMatchesSearch";
import { toQueryLogFilters } from "../../lib/toQueryLogFilters";
import type { AnalyticsWindow, QueryRow } from "../../types";
import QueryFilterBar, { type QueryFilterValue } from "../QueryFilterBar";
import QueryRowDetail from "../QueryRowDetail";
import TableControlsBar from "../TableControlsBar";
import ExpandedDetailCell from "./ExpandedDetailCell";

const fmtLatency = (ms: number) => `${(ms / 1000).toFixed(1)}s`;

const latencyClass = (ms: number) => {
  const s = ms / 1000;
  if (s > 20) return "text-red-600 font-semibold";
  if (s > 10) return "text-amber-600 font-medium";
  return "text-slate-700";
};

const COLS = 14;

// Sort keys map 1:1 to the API `sort` param. The API sorts tokens only by
// `total_tokens` — input/output are not sortable server-side, so they stay
// plain columns (sending sort=input_tokens 422s and the table fails to load).
type SortKey = "created_at" | "latency_ms" | "cost_usd" | "total_tokens";

function SortHeader({
  col,
  label,
  sortKey,
  sortOrder,
  onSort,
  className,
}: {
  col: SortKey;
  label: React.ReactNode;
  sortKey: SortKey;
  sortOrder: "asc" | "desc";
  onSort: (col: SortKey) => void;
  className?: string;
}) {
  return (
    <th
      className={cn("px-2 py-2.5 cursor-pointer select-none", className)}
      onClick={() => onSort(col)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        <ChevronsUpDown
          className={cn(
            "size-3",
            sortKey === col ? "text-indigo-400" : "text-slate-300",
          )}
        />
      </span>
    </th>
  );
}

export interface QualityQueryTableProps {
  agentId?: string | null;
  window: AnalyticsWindow;
  filterValue: QueryFilterValue;
  onFilterChange: (v: QueryFilterValue) => void;
  tableRef?: React.Ref<HTMLDivElement>;
  /** Starting sort column (remount with a fresh `key` to re-apply). */
  initialSort?: SortKey;
}

export default function QualityQueryTable({
  agentId,
  window,
  filterValue,
  onFilterChange,
  tableRef,
  initialSort = "created_at",
}: QualityQueryTableProps) {
  const { limits } = useAnalyticsScope();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(limits.queriesPageSize);
  const [sort, setSort] = useState<SortKey>(initialSort);
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [expanded, setExpanded] = useState<string | null>(null);
  // Node Trace toggle — when ON, expanded rows include the per-node breakdown.
  const [traceOn, setTraceOn] = useState(false);

  // Reset page and expanded row when filters change
  const filterKey = JSON.stringify(filterValue);
  useEffect(() => {
    setPage(0);
    setExpanded(null);
  }, [filterKey]);

  const q = useQueryLog(agentId, window, {
    sort,
    order,
    filters: toQueryLogFilters(filterValue),
    limit: pageSize,
    offset: page * pageSize,
  });

  // `search` filters the loaded page client-side (API has no free-text search).
  const allRows: QueryRow[] = q.data?.queries ?? [];
  const rows = filterValue.search
    ? allRows.filter((r) => rowMatchesSearch(r, filterValue.search ?? ""))
    : allRows;
  const totalItems = q.data?.total_matching ?? 0;

  function handleSort(col: SortKey) {
    if (col === sort) {
      setOrder((o) => (o === "desc" ? "asc" : "desc"));
    } else {
      setSort(col);
      setOrder("desc");
    }
    setPage(0);
    setExpanded(null);
  }

  return (
    <div
      ref={tableRef}
      className="rounded-lg border border-gray-200 bg-white shadow-sm"
    >
      {/* Filter bar */}
      <div className="px-4 pt-4">
        <QueryFilterBar value={filterValue} onChange={onFilterChange} />
      </div>

      {/* Node Trace control — rows-per-page lives in the pagination footer. */}
      <div className="px-4 pt-3">
        <TableControlsBar traceOn={traceOn} onTraceToggle={setTraceOn} />
      </div>

      {traceOn && (
        <div className="mx-4 mt-3 rounded-md border border-indigo-200 bg-indigo-50 px-3.5 py-2.5 text-xs text-indigo-700">
          <strong>Node Trace ON</strong> — expand any row (▶) to see the
          per-node breakdown.
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto mt-2">
        <table className="w-full min-w-[960px]">
          <thead>
            <tr className="border-b border-slate-100 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              <th className="w-8 px-2 py-2.5" />
              <th className="px-2 py-2.5 w-10">#</th>
              <th className="px-2 py-2.5">Query</th>
              <th className="px-2 py-2.5">Status</th>
              <SortHeader
                col="latency_ms"
                label="Latency"
                sortKey={sort}
                sortOrder={order}
                onSort={handleSort}
                className="text-right"
              />
              <SortHeader
                col="cost_usd"
                label="Cost"
                sortKey={sort}
                sortOrder={order}
                onSort={handleSort}
                className="text-right"
              />
              <th className="px-2 py-2.5 text-right">Input</th>
              <th className="px-2 py-2.5 text-right">Output</th>
              <SortHeader
                col="total_tokens"
                label="Total"
                sortKey={sort}
                sortOrder={order}
                onSort={handleSort}
                className="text-right"
              />
              <th className="px-2 py-2.5">Services</th>
              <th className="px-2 py-2.5">Feedback</th>
              <th className="px-2 py-2.5">Chat ID</th>
              <th className="px-2 py-2.5">Session ID</th>
              <SortHeader
                col="created_at"
                label="Date & Time"
                sortKey={sort}
                sortOrder={order}
                onSort={handleSort}
              />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {q.isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: COLS }).map((__, c) => (
                    <td key={c} className="px-2 py-3">
                      <div className="h-4 rounded bg-slate-100 animate-pulse" />
                    </td>
                  ))}
                </tr>
              ))
            ) : q.isError ? (
              <tr>
                <td
                  colSpan={COLS}
                  className="px-4 py-10 text-center text-sm text-red-500"
                >
                  Failed to load queries. Try again.
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td
                  colSpan={COLS}
                  className="px-4 py-10 text-center text-sm text-slate-400"
                >
                  No queries match the current filters.
                </td>
              </tr>
            ) : (
              rows.map((r, i) => {
                const id = r.chat_id ?? r.trace_id ?? String(i);
                const isOpen = expanded === id;
                return (
                  <Fragment key={id}>
                    <tr
                      className="cursor-pointer hover:bg-slate-50 transition-colors"
                      onClick={() => setExpanded(isOpen ? null : id)}
                    >
                      <td className="px-2 py-3 text-slate-400">
                        <ChevronRight
                          className={cn(
                            "size-4 transition-transform",
                            isOpen && "rotate-90",
                          )}
                        />
                      </td>
                      <td className="px-2 py-3 text-slate-400">
                        {page * pageSize + i + 1}
                      </td>
                      <td className="px-2 py-3">
                        <span
                          className="block max-w-[240px] truncate text-slate-700"
                          title={r.query}
                        >
                          {r.query}
                        </span>
                      </td>
                      <td className="px-2 py-3">
                        <span
                          className={cn(
                            "text-xs font-medium rounded px-1.5 py-0.5",
                            r.is_answered
                              ? "text-emerald-700 bg-emerald-50"
                              : "text-red-700 bg-red-50",
                          )}
                        >
                          {r.is_answered ? "answered" : r.status}
                        </span>
                      </td>
                      <td className="px-2 py-3 text-right">
                        <span className={latencyClass(r.metrics.latency_ms)}>
                          {fmtLatency(r.metrics.latency_ms)}
                        </span>
                      </td>
                      <td className="px-2 py-3 text-right text-slate-600">
                        ${r.metrics.cost_usd.toFixed(4)}
                      </td>
                      <td className="px-2 py-3 text-right text-slate-600">
                        {r.metrics.input_tokens.toLocaleString()}
                      </td>
                      <td className="px-2 py-3 text-right text-slate-600">
                        {r.metrics.output_tokens.toLocaleString()}
                      </td>
                      <td className="px-2 py-3 text-right text-slate-600">
                        {r.metrics.total_tokens.toLocaleString()}
                      </td>
                      <td className="px-2 py-3">
                        {r.services_used.length ? (
                          <div className="flex flex-wrap gap-1">
                            {r.services_used.map((s) => (
                              <span
                                key={s}
                                className="text-[11px] font-medium text-indigo-700 bg-indigo-50 rounded px-1.5 py-0.5"
                              >
                                {s}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </td>
                      <td className="px-2 py-3 text-sm">
                        {feedbackEmoji(r.feedback)}
                      </td>
                      <td className="px-2 py-3">
                        <span
                          className="font-mono text-xs text-indigo-600 whitespace-nowrap"
                          title={r.chat_id ?? undefined}
                        >
                          {r.chat_id || "—"}
                        </span>
                      </td>
                      <td className="px-2 py-3">
                        <span
                          className="font-mono text-xs text-slate-500"
                          title={r.session_id ?? undefined}
                        >
                          {r.session_id ?? "—"}
                        </span>
                      </td>
                      <td className="px-2 py-3 text-xs text-slate-500 whitespace-nowrap">
                        {dayjs(r.timestamp).format("MMM D, YYYY h:mm A")}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-slate-50/70">
                        <ExpandedDetailCell colSpan={COLS}>
                          <QueryRowDetail row={r} showNodeTrace={traceOn} />
                        </ExpandedDetailCell>
                      </tr>
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination footer */}
      {totalItems > 0 && (
        <TablePagination
          pageIndex={page}
          pageSize={pageSize}
          totalItems={totalItems}
          onPageChange={({ pageIndex, pageSize: nextSize }) => {
            setPageSize(nextSize);
            setPage(pageIndex);
          }}
          className="border-t border-slate-100"
        />
      )}
    </div>
  );
}
