"use client";

import { Fragment, useMemo, useState } from "react";

import dayjs from "dayjs";
import { ArrowDown, ArrowUp, ChevronRight, ChevronsUpDown } from "lucide-react";

import { TablePagination } from "@/platform/components/ui";
import { cn } from "@/platform/utils";

import { feedbackEmoji } from "../../lib/normalizeFeedback";
import { rowMatchesSearch } from "../../lib/rowMatchesSearch";
import type { QueryRow } from "../../types";
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

const COLS = 11;

// Sort keys map 1:1 to the API `sort` param.
export type SlowestSortKey = "created_at" | "latency_ms" | "cost_usd";

function SortableTh({
  col,
  label,
  sort,
  order,
  onSort,
  className,
}: {
  col: SlowestSortKey;
  label: string;
  sort?: SlowestSortKey;
  order?: "asc" | "desc";
  onSort?: (col: SlowestSortKey) => void;
  className?: string;
}) {
  const active = sort === col;
  return (
    <th
      className={cn(
        "px-2 py-2.5",
        onSort && "cursor-pointer select-none hover:text-slate-600",
        active && "text-indigo-600",
        className,
      )}
      onClick={onSort ? () => onSort(col) : undefined}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active ? (
          order === "asc" ? (
            <ArrowUp className="size-3 text-indigo-500" />
          ) : (
            <ArrowDown className="size-3 text-indigo-500" />
          )
        ) : (
          <ChevronsUpDown className="size-3 text-slate-300" />
        )}
      </span>
    </th>
  );
}

export default function SlowestQueriesTable({
  rows,
  isLoading,
  title,
  subtitle,
  total,
  pageIndex = 0,
  pageSize = 10,
  onPageChange,
  search = "",
  sort,
  order,
  onSort,
}: {
  rows: QueryRow[];
  isLoading?: boolean;
  title?: string;
  subtitle?: string;
  total?: number;
  pageIndex?: number;
  pageSize?: number;
  onPageChange?: (p: { pageIndex: number; pageSize: number }) => void;
  search?: string;
  sort?: SlowestSortKey;
  order?: "asc" | "desc";
  onSort?: (col: SlowestSortKey) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  // Node Trace toggle — when ON, expanded rows include the per-node breakdown.
  const [traceOn, setTraceOn] = useState(false);

  // `search` (from the filter bar) filters the loaded rows client-side —
  // the API has no free-text search.
  const filtered = useMemo(() => {
    if (!search.trim()) return rows;
    return rows.filter((r) => rowMatchesSearch(r, search));
  }, [rows, search]);

  const totalItems = total ?? rows.length;
  const showFooter = !!onPageChange && totalItems > 0;

  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
      {/* Header: optional title/subtitle + Node Trace toggle (search lives in
          the filter bar above). The controls row always renders so the toggle
          shows even when the parent owns the title. */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-slate-100">
        <div>
          {title && (
            <h4 className="text-sm font-semibold text-slate-700">{title}</h4>
          )}
          {subtitle && (
            <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>
          )}
        </div>
        {/* Rows-per-page lives in the pagination footer with the rest of the
            page controls. */}
        <TableControlsBar traceOn={traceOn} onTraceToggle={setTraceOn} />
      </div>

      {traceOn && (
        <div className="mx-4 mt-3 rounded-md border border-indigo-200 bg-indigo-50 px-3.5 py-2.5 text-xs text-indigo-700">
          <strong>Node Trace ON</strong> — expand any row (▶) to see the
          per-node breakdown.
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px]">
          <thead>
            <tr className="border-b border-slate-100 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              <th className="w-8 px-2 py-2.5" />
              <th className="px-2 py-2.5 w-10">#</th>
              <th className="px-2 py-2.5">Query</th>
              <SortableTh
                col="latency_ms"
                label="Latency"
                sort={sort}
                order={order}
                onSort={onSort}
                className="text-right"
              />
              <SortableTh
                col="cost_usd"
                label="Cost"
                sort={sort}
                order={order}
                onSort={onSort}
                className="text-right"
              />
              <th className="px-2 py-2.5">Status</th>
              <th className="px-2 py-2.5">Services</th>
              <th className="px-2 py-2.5">Feedback</th>
              <th className="px-2 py-2.5">Chat ID</th>
              <th className="px-2 py-2.5">Session ID</th>
              <SortableTh
                col="created_at"
                label="Date & Time"
                sort={sort}
                order={order}
                onSort={onSort}
              />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: COLS }).map((__, c) => (
                    <td key={c} className="px-2 py-3">
                      <div className="h-4 rounded bg-slate-100 animate-pulse" />
                    </td>
                  ))}
                </tr>
              ))
            ) : filtered.length === 0 ? (
              <tr>
                <td
                  colSpan={COLS}
                  className="px-4 py-10 text-center text-sm text-slate-400"
                >
                  {search.trim()
                    ? "No queries match your search."
                    : "No queries in this window."}
                </td>
              </tr>
            ) : (
              filtered.map((r, i) => {
                const id = r.chat_id || String(i);
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
                        {pageIndex * pageSize + i + 1}
                      </td>
                      <td className="px-2 py-3">
                        <span
                          className="block max-w-[260px] truncate text-slate-700"
                          title={r.query}
                        >
                          {r.query}
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
                          className="font-mono text-xs text-slate-400 whitespace-nowrap"
                          title={r.session_id ?? undefined}
                        >
                          {r.session_id || "—"}
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

      {/* Numbered pagination */}
      {showFooter && (
        <TablePagination
          pageIndex={pageIndex}
          pageSize={pageSize}
          totalItems={totalItems}
          onPageChange={(p) => onPageChange?.(p)}
          className="border-t border-slate-100"
        />
      )}
    </div>
  );
}
