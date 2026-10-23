"use client";

import { Search } from "lucide-react";

import { useAnalyticsScope } from "../AnalyticsScope";
import { isHiddenService } from "../lib/hiddenServices";

/**
 * Every option below comes from the scope, which publishes the vocabulary its
 * backend actually accepts (`AnalyticsScope.filterOptions`). The lists used to
 * be written here, which meant the NL2SQL screen offered the agent
 * dashboard's service families as NL2SQL `route` values and filtered every row
 * away without reporting anything.
 */
export interface QueryFilterValue {
  status?: string;
  // Binary answered/unanswered filter. Maps to the API `outcome` param
  // ("success" | "failure"); `status` is too granular to express "all
  // unanswered" (no_answer + error + …) in one value.
  outcome?: string;
  services_used?: string;
  feedback?: string;
  min_cost_usd?: number;
  min_latency_ms?: number;
  search?: string;
}

export interface QueryFilterBarProps {
  value: QueryFilterValue;
  onChange: (next: QueryFilterValue) => void;
}

const selectCls =
  "rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:border-indigo-500 focus:outline-none";
const labelCls =
  "text-[10px] font-semibold uppercase tracking-wide text-slate-500";

export default function QueryFilterBar({
  value,
  onChange,
}: QueryFilterBarProps) {
  const { hiddenServices, filterOptions } = useAnalyticsScope();
  const patch = (p: Partial<QueryFilterValue>) => onChange({ ...value, ...p });
  /**
   * A backend that filters on the literal `status` publishes those values and
   * no `outcome` ones; the control has to send whichever parameter the scope's
   * vocabulary belongs to, because the other one is ignored rather than
   * refused (see `AnalyticsFilterOptions.statuses`).
   */
  const usesStatus = filterOptions.statuses.length > 0;
  const statusOptions = usesStatus
    ? filterOptions.statuses
    : filterOptions.outcomes;
  const services = filterOptions.routes.filter(
    (o) => !isHiddenService(o.value, hiddenServices),
  );

  return (
    <div className="flex flex-wrap items-end gap-2.5 rounded-lg border border-gray-200 bg-slate-50 p-4">
      <div className="flex flex-col gap-1">
        <label className={labelCls} htmlFor="qfb-status">
          Status
        </label>
        <select
          id="qfb-status"
          aria-label="Status"
          className={selectCls}
          value={(usesStatus ? value.status : value.outcome) ?? ""}
          onChange={(e) =>
            patch(
              usesStatus
                ? { status: e.target.value || undefined }
                : { outcome: e.target.value || undefined },
            )
          }
        >
          <option value="">All Statuses</option>
          {statusOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label className={labelCls} htmlFor="qfb-service">
          {filterOptions.routesLabel}
        </label>
        <select
          id="qfb-service"
          aria-label={filterOptions.routesLabel}
          className={selectCls}
          value={value.services_used ?? ""}
          onChange={(e) =>
            patch({ services_used: e.target.value || undefined })
          }
        >
          <option value="">All</option>
          {services.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label className={labelCls} htmlFor="qfb-feedback">
          Feedback
        </label>
        <select
          id="qfb-feedback"
          aria-label="Feedback"
          className={selectCls}
          value={value.feedback ?? ""}
          onChange={(e) => patch({ feedback: e.target.value || undefined })}
        >
          <option value="">All Feedback</option>
          {filterOptions.feedback.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label className={labelCls} htmlFor="qfb-cost">
          Cost above ($)
        </label>
        <input
          id="qfb-cost"
          aria-label="Cost above ($)"
          type="number"
          step="0.001"
          placeholder="e.g. 0.05"
          className={`${selectCls} w-28`}
          value={value.min_cost_usd ?? ""}
          onChange={(e) =>
            patch({
              min_cost_usd: e.target.value ? Number(e.target.value) : undefined,
            })
          }
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className={labelCls} htmlFor="qfb-lat">
          Latency above (seconds)
        </label>
        <input
          id="qfb-lat"
          aria-label="Latency above (seconds)"
          type="number"
          step="0.1"
          placeholder="e.g. 5"
          className={`${selectCls} w-28`}
          value={
            value.min_latency_ms != null ? value.min_latency_ms / 1000 : ""
          }
          onChange={(e) =>
            patch({
              min_latency_ms: e.target.value
                ? Math.round(Number(e.target.value) * 1000)
                : undefined,
            })
          }
        />
      </div>

      <div className="ml-auto flex items-end gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3 -translate-y-1/2 text-slate-400" />
          <input
            aria-label="Search"
            placeholder="Search anything…"
            className={`${selectCls} w-56 pl-7`}
            value={value.search ?? ""}
            onChange={(e) => patch({ search: e.target.value || undefined })}
          />
        </div>
        <button
          type="button"
          onClick={() => onChange({})}
          className="rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-500 hover:text-slate-700"
        >
          ✕ Clear
        </button>
      </div>
    </div>
  );
}
