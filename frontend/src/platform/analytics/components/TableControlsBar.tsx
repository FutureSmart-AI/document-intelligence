"use client";

import { cn } from "@/platform/utils";

export const ROWS_PER_PAGE_OPTIONS = [10, 25, 50] as const;

/**
 * Rows-per-page select + Node Trace switch shown above the query tables.
 * The Node Trace toggle controls whether expanded rows include the per-node
 * breakdown section. Rows-per-page is optional — Load-More tables (Slowest /
 * Most Expensive) omit it and show only the toggle.
 */
export default function TableControlsBar({
  rowsPerPage,
  onRowsPerPageChange,
  traceOn,
  onTraceToggle,
}: {
  rowsPerPage?: number;
  onRowsPerPageChange?: (n: number) => void;
  traceOn: boolean;
  onTraceToggle: (on: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-end gap-2.5">
      {rowsPerPage != null && onRowsPerPageChange && (
        <label className="flex items-center gap-1.5 text-xs text-slate-500">
          Rows/page:
          <select
            aria-label="Rows per page"
            className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-slate-700 focus:border-indigo-500 focus:outline-none"
            value={rowsPerPage}
            onChange={(e) => onRowsPerPageChange(Number(e.target.value))}
          >
            {ROWS_PER_PAGE_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      )}

      <button
        type="button"
        role="switch"
        aria-checked={traceOn}
        aria-label="Node Trace"
        onClick={() => onTraceToggle(!traceOn)}
        className="flex cursor-pointer items-center gap-2 rounded-md border border-gray-200 bg-slate-50 px-2.5 py-1"
      >
        <span
          className={cn(
            "text-xs font-semibold",
            traceOn ? "text-indigo-600" : "text-slate-500",
          )}
        >
          Node Trace
        </span>
        <span
          className={cn(
            "relative h-[18px] w-[34px] rounded-full transition-colors",
            traceOn ? "bg-indigo-600" : "bg-slate-300",
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 size-3.5 rounded-full bg-white shadow transition-all",
              traceOn ? "left-[18px]" : "left-0.5",
            )}
          />
        </span>
      </button>
    </div>
  );
}
