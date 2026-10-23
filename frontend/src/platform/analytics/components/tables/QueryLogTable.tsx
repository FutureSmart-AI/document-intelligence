"use client";

import { Fragment, useMemo, useState } from "react";
import type React from "react";

import dayjs from "dayjs";
import { ArrowDown, ArrowUp, ChevronRight, ChevronsUpDown } from "lucide-react";

import { TablePagination } from "@/platform/components/ui";
import { cn } from "@/platform/utils";

import { useAnalyticsScope } from "../../AnalyticsScope";
import { feedbackEmoji } from "../../lib/normalizeFeedback";
import type { QueryRow } from "../../types";
import QueryRowDetail from "../QueryRowDetail";
import TableControlsBar from "../TableControlsBar";
import ExpandedDetailCell from "./ExpandedDetailCell";

// Sort keys map 1:1 to the API `sort` param (created_at | latency_ms | cost_usd | total_tokens).
export type QueryLogSortKey =
  "created_at" | "latency_ms" | "cost_usd" | "total_tokens";

const COLS = 14;

function SortHeader({
  col,
  label,
  activeKey,
  order,
  onSort,
  className,
}: {
  col: QueryLogSortKey;
  label: React.ReactNode;
  activeKey: QueryLogSortKey;
  order: "asc" | "desc";
  onSort?: (col: QueryLogSortKey) => void;
  className?: string;
}) {
  const active = activeKey === col;
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

export default function QueryLogTable({
  data,
  total,
  isLoading,
  pageIndex,
  pageSize,
  onPageChange,
  title,
  subtitle,
  sort = "created_at",
  order = "desc",
  onSort,
  search = "",
  initialExpandedId,
}: {
  data: QueryRow[];
  total: number;
  isLoading?: boolean;
  pageIndex: number;
  pageSize: number;
  onPageChange: (p: { pageIndex: number; pageSize: number }) => void;
  title?: string;
  subtitle?: string;
  sort?: QueryLogSortKey;
  order?: "asc" | "desc";
  onSort?: (col: QueryLogSortKey) => void;
  search?: string;
  /**
   * Row to open on mount, for the `?chat_id=` deep link — arriving from a
   * conversation should land on the query already expanded, not on a
   * one-row table the reader still has to click.
   */
  initialExpandedId?: string | null;
}) {
  const { filterOptions } = useAnalyticsScope();
  const [expanded, setExpanded] = useState<string | null>(
    initialExpandedId ?? null,
  );
  // Node Trace toggle — when ON, expanded rows include the per-node breakdown.
  const [traceOn, setTraceOn] = useState(false);

  // Client-side search over the loaded page (the API has no free-text search).
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return data;
    return data.filter(
      (r) =>
        r.query.toLowerCase().includes(q) ||
        (r.chat_id ?? "").toLowerCase().includes(q) ||
        (r.session_id ?? "").toLowerCase().includes(q) ||
        r.services_used.some((s) => s.toLowerCase().includes(q)) ||
        String(r.metrics.cost_usd).includes(q),
    );
  }, [data, search]);

  const totalItems = total;

  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
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

      <div className="overflow-x-auto">
        <table className="w-full min-w-[1040px]">
          <thead>
            <tr className="border-b border-slate-100 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              <th className="w-8 px-2 py-2.5" />
              <th className="px-2 py-2.5 w-10">#</th>
              <th className="px-2 py-2.5">Query</th>
              <th className="px-2 py-2.5">Status</th>
              <SortHeader
                col="latency_ms"
                label="Latency"
                activeKey={sort}
                order={order}
                onSort={onSort}
                className="text-right"
              />
              <SortHeader
                col="cost_usd"
                label="Cost"
                activeKey={sort}
                order={order}
                onSort={onSort}
                className="text-right"
              />
              <th className="px-2 py-2.5 text-right">Input Tokens</th>
              <th className="px-2 py-2.5 text-right">Output Tokens</th>
              <SortHeader
                col="total_tokens"
                label="Total Tokens"
                activeKey={sort}
                order={order}
                onSort={onSort}
                className="text-right"
              />
              <th className="px-2 py-2.5">Services</th>
              <th className="px-2 py-2.5">Feedback</th>
              <th className="px-2 py-2.5">Chat ID</th>
              <th className="px-2 py-2.5">Session ID</th>
              <SortHeader
                col="created_at"
                label="Date & Time"
                activeKey={sort}
                order={order}
                onSort={onSort}
              />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading ? (
              Array.from({ length: 10 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: COLS }).map((__, c) => (
                    <td key={c} className="px-2 py-3">
                      <div className="h-4 rounded bg-slate-100 animate-pulse" />
                    </td>
                  ))}
                </tr>
              ))
            ) : rows.length === 0 ? (
              <tr>
                <td
                  colSpan={COLS}
                  className="px-4 py-10 text-center text-sm text-slate-400"
                >
                  No queries match these filters.
                </td>
              </tr>
            ) : (
              rows.map((r, i) => {
                const id = r.chat_id || r.trace_id || String(i);
                const isOpen = expanded === id;
                const c = r.metrics;
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
                        <span
                          className={
                            c.latency_ms / 1000 > 10
                              ? "text-amber-600 font-medium"
                              : ""
                          }
                        >
                          {(c.latency_ms / 1000).toFixed(1)}s
                        </span>
                      </td>
                      <td className="px-2 py-3 text-right">
                        <span
                          className={cn(
                            "text-slate-700",
                            !c.cost_complete && "text-amber-600",
                          )}
                          title={
                            c.cost_complete
                              ? undefined
                              : "Unpriced — refresh costs to sync the LangSmith figure (shown value is a lower bound)"
                          }
                        >
                          {c.cost_complete ? "" : "~"}${c.cost_usd.toFixed(4)}
                        </span>
                      </td>
                      <td className="px-2 py-3 text-right text-slate-600">
                        {c.input_tokens.toLocaleString()}
                      </td>
                      <td className="px-2 py-3 text-right text-slate-600">
                        {c.output_tokens.toLocaleString()}
                      </td>
                      <td className="px-2 py-3 text-right text-slate-600">
                        {c.total_tokens.toLocaleString()}
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

      {totalItems > 0 && (
        <TablePagination
          pageIndex={pageIndex}
          pageSize={pageSize}
          totalItems={totalItems}
          onPageChange={onPageChange}
          // Bounded by what the backend caps `limit` at, not by the shared UI
          // default — a page larger than the cap is a 422, not a long page.
          pageSizeOptions={[...filterOptions.rowsPerPage]}
          className="border-t border-slate-100"
        />
      )}
    </div>
  );
}
