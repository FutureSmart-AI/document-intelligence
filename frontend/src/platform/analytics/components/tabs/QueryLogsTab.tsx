"use client";

import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { UseQueryResult } from "@tanstack/react-query";
import { CornerUpLeft, Search, X } from "lucide-react";

import { cn } from "@/platform/utils";

import { useAnalyticsScope } from "../../AnalyticsScope";
import { useQueryLog } from "../../hooks/useQueryLog";
import { isHiddenService } from "../../lib/hiddenServices";
import type { AnalyticsWindow, QueryLogFilters, Scorecard } from "../../types";
import QueryLogTable, { type QueryLogSortKey } from "../tables/QueryLogTable";

/**
 * Every value this tab can send is published by the scope, never written here.
 *
 * The two products behind this screen accept different words for the same
 * filter — the dashboard's `route` is a service family, NL2SQL's is a query
 * plan — and the list that used to sit at this spot was the dashboard's, sent
 * on both. On the NL2SQL screen that made every option in the dropdown a route
 * the service has never heard of, and the log came back empty with no error to
 * say why. See `AnalyticsScope.filterOptions`.
 */
const fieldClass =
  "rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/30";

export default function QueryLogsTab({
  window,
  agentId,
}: {
  scorecard: UseQueryResult<Scorecard>;
  window: AnalyticsWindow;
  agentId?: string | null;
}) {
  // A product that does not own Knowledge must not offer it as a filter, and
  // must not keep a stale value for it selected.
  const { hiddenServices, filterOptions, limits } = useAnalyticsScope();
  const serviceOptions = filterOptions.routes.filter(
    (o) => !isHiddenService(o.value, hiddenServices),
  );
  const outcomeValues = new Set(filterOptions.outcomes.map((o) => o.value));
  /**
   * The Status dropdown offers whichever vocabulary its backend filters on.
   *
   * `outcome` is the coarse answered/unanswered pair; `status` is the literal
   * per-query status. A backend that only understands the second ignores the
   * first silently — the log comes back whole — so the choice of parameter has
   * to follow the vocabulary the scope published, not the other way round.
   */
  const statusValues = new Set(filterOptions.statuses.map((o) => o.value));
  const statusOptions = filterOptions.statuses.length
    ? filterOptions.statuses
    : filterOptions.outcomes;
  const [status, setStatus] = useState("");
  const [service, setService] = useState("");
  const [feedback, setFeedback] = useState("");
  const [minCost, setMinCost] = useState("");
  const [minLatencySec, setMinLatencySec] = useState("");
  const [search, setSearch] = useState("");
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState(limits.queriesPageSize);
  const [sort, setSort] = useState<QueryLogSortKey>("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");

  const resetPaging = () => setPageIndex(0);

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const handleSort = (col: QueryLogSortKey) => {
    if (col === sort) {
      setOrder((o) => (o === "desc" ? "asc" : "desc"));
    } else {
      setSort(col);
      setOrder("desc");
    }
    resetPaging();
  };

  /**
   * `?chat_id=` — the deep link from a conversation to the one query behind it.
   *
   * The API treats `chat_id` as an exact lookup that **ignores the time
   * window**, so the toolbar's period says nothing about what is on screen
   * here; the notice below says so, and offers the way back to the full log.
   * Every other filter is suppressed while it is set — combining them could
   * only ever return the same row or nothing.
   */
  const pinnedChatId = searchParams.get("chat_id");

  const filters: QueryLogFilters = {};
  // Binary answered/unanswered → `outcome`; granular literals → `status`.
  if (statusValues.has(status)) filters.status = status;
  else if (outcomeValues.has(status)) filters.outcome = status;
  else if (status) filters.status = status;
  // Service selector → API `route` filter (no `services_used` param exists).
  if (service) filters.route = service;
  // The option value is the API value — the scope publishes the vocabulary the
  // endpoint matches on, so there is nothing left to translate.
  if (feedback) filters.feedback = feedback;
  const cost = parseFloat(minCost);
  if (!Number.isNaN(cost) && minCost !== "") filters.min_cost_usd = cost;
  const latSec = parseFloat(minLatencySec);
  if (!Number.isNaN(latSec) && minLatencySec !== "")
    filters.min_latency_ms = Math.round(latSec * 1000);
  // `search` is applied client-side over the loaded page (the API has no
  // free-text search — only exact chat_id lookup), so it is not sent here.

  const ql = useQueryLog(agentId, window, {
    sort,
    order,
    filters: pinnedChatId ? { chat_id: pinnedChatId } : filters,
    limit: pageSize,
    offset: pinnedChatId ? 0 : pageIndex * pageSize,
  });

  const clearPinned = () => {
    const p = new URLSearchParams(searchParams.toString());
    p.delete("chat_id");
    router.replace(`${pathname}?${p.toString()}`, { scroll: false });
  };

  const hasFilters = !!(
    status ||
    service ||
    feedback ||
    minCost ||
    minLatencySec ||
    search
  );

  return (
    <div className="space-y-4">
      {pinnedChatId && (
        <div
          role="status"
          className="flex items-start gap-2.5 rounded-lg border border-indigo-200 bg-indigo-50 p-3"
        >
          <CornerUpLeft className="mt-0.5 size-4 shrink-0 text-indigo-500" />
          <div className="min-w-0 text-sm">
            <span className="font-semibold text-indigo-900">
              Showing one query
            </span>
            <span className="ml-1.5 text-indigo-800">
              opened by link, so the date window above does not apply to it.
            </span>
            <button
              type="button"
              onClick={clearPinned}
              className="ml-2 cursor-pointer font-medium text-indigo-600 underline underline-offset-2 hover:text-indigo-700"
            >
              Show the full log
            </button>
          </div>
        </div>
      )}

      {!pinnedChatId && (
        <div className="flex flex-wrap items-end gap-3 pb-2 border-b border-slate-100">
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-500 uppercase tracking-wide">
            Status
            <select
              aria-label="Status"
              className={fieldClass}
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                resetPaging();
              }}
            >
              <option value="">All Statuses</option>
              {statusOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs font-medium text-slate-500 uppercase tracking-wide">
            {filterOptions.routesLabel}
            <select
              aria-label={filterOptions.routesLabel}
              className={fieldClass}
              value={service}
              onChange={(e) => {
                setService(e.target.value);
                resetPaging();
              }}
            >
              <option value="">All</option>
              {serviceOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs font-medium text-slate-500 uppercase tracking-wide">
            Feedback
            <select
              aria-label="Feedback"
              className={fieldClass}
              value={feedback}
              onChange={(e) => {
                setFeedback(e.target.value);
                resetPaging();
              }}
            >
              <option value="">All Feedback</option>
              {filterOptions.feedback.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs font-medium text-slate-500 uppercase tracking-wide">
            Cost Above ($)
            <input
              aria-label="Cost above (dollars)"
              type="number"
              step="0.01"
              min="0"
              placeholder="e.g. 0.05"
              className={cn(fieldClass, "w-28")}
              value={minCost}
              onChange={(e) => {
                setMinCost(e.target.value);
                resetPaging();
              }}
            />
          </label>

          <label className="flex flex-col gap-1 text-xs font-medium text-slate-500 uppercase tracking-wide">
            Latency Above (seconds)
            <input
              aria-label="Min latency (seconds)"
              type="number"
              step="0.5"
              min="0"
              placeholder="e.g. 5"
              className={cn(fieldClass, "w-28")}
              value={minLatencySec}
              onChange={(e) => {
                setMinLatencySec(e.target.value);
                resetPaging();
              }}
            />
          </label>

          {/* Search + Clear pushed to the right */}
          <div className="ml-auto flex flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Search — Query Text, Chat ID, Session ID, Cost, Services
            </span>
            <div className="flex items-stretch">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" />
                <input
                  aria-label="Search queries"
                  type="text"
                  placeholder="Search anything..."
                  className={cn(fieldClass, "w-64 pl-8 rounded-r-none")}
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    resetPaging();
                  }}
                />
              </div>
              <button
                type="button"
                disabled={!hasFilters}
                onClick={() => {
                  setStatus("");
                  setService("");
                  setFeedback("");
                  setMinCost("");
                  setMinLatencySec("");
                  setSearch("");
                  resetPaging();
                }}
                className={cn(
                  "inline-flex h-[34px] items-center gap-1 rounded-md rounded-l-none border border-l-0 border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50",
                  !hasFilters && "cursor-not-allowed opacity-50 hover:bg-white",
                )}
              >
                <X className="size-3.5" />
                Clear
              </button>
            </div>
          </div>
        </div>
      )}

      <QueryLogTable
        title={pinnedChatId ? "One Query" : "All Queries"}
        subtitle={
          pinnedChatId
            ? "Linked from a conversation — this row carries its SQL, tables and per-node trace."
            : `Showing all ${ql.data?.total_matching?.toLocaleString() ?? "…"} queries — click column header to sort, click row to expand`
        }
        initialExpandedId={pinnedChatId}
        data={ql.data?.queries ?? []}
        total={ql.data?.total_matching ?? 0}
        isLoading={ql.isLoading}
        pageIndex={pageIndex}
        pageSize={pageSize}
        onPageChange={(p) => {
          setPageIndex(p.pageIndex);
          setPageSize(p.pageSize);
        }}
        sort={sort}
        order={order}
        onSort={handleSort}
        search={search}
      />
    </div>
  );
}
