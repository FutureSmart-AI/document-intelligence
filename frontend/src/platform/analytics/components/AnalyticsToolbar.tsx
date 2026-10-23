import dayjs from "dayjs";
import { Calendar } from "lucide-react";

import { DateRangeSelector } from "@/platform/components/ui";
import type { DateRange } from "@/platform/components/ui/DateRangeSelector";
import { cn } from "@/platform/utils";

import { useAnalyticsScope } from "../AnalyticsScope";
import type { AnalyticsWindow } from "../types";

function TabBtn({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "cursor-pointer px-3 py-2 text-sm font-medium -mb-px border-b-2 transition-colors",
        active
          ? "border-indigo-600 text-indigo-600"
          : "border-transparent text-slate-500 hover:text-slate-700",
      )}
    >
      {label}
    </button>
  );
}

function DateRangeDisplay({
  since,
  until,
  previousSince,
}: {
  since?: string;
  until?: string;
  previousSince?: string;
}) {
  if (!since || !until) return null;
  const fmt = (d: string) => dayjs(d).format("MMM D");
  const fmtYear = (d: string) => dayjs(d).format("MMM D, YYYY");
  const prevEnd = previousSince
    ? dayjs(since).subtract(1, "day").format("YYYY-MM-DD")
    : null;
  return (
    <div className="flex items-center gap-2 text-xs text-slate-500">
      <Calendar className="size-3.5 text-slate-400" />
      <span className="text-slate-700 font-medium">
        {fmt(since)} – {fmtYear(until)}
      </span>
      {previousSince && prevEnd && (
        <span className="text-slate-400">
          vs {fmt(previousSince)} – {fmt(prevEnd)}
        </span>
      )}
    </div>
  );
}

/**
 * How finely the usage series is bucketed.
 *
 * Rendered only when the scope publishes a bucket vocabulary — the agent
 * dashboard's scorecard takes no such parameter, and an analytics parameter an
 * endpoint has not whitelisted comes back 422 rather than ignored. "Auto"
 * sends nothing and lets the service pick, which is also what a link with no
 * `?bucket=` means.
 */
function GranularitySelect({
  bucket,
  onBucketChange,
}: {
  bucket?: string;
  onBucketChange: (b: string | undefined) => void;
}) {
  const { filterOptions } = useAnalyticsScope();
  if (!filterOptions.buckets.length) return null;
  return (
    <label className="flex items-center gap-1.5 text-xs text-slate-500">
      Group by
      <select
        aria-label="Group by"
        className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-slate-700 focus:border-indigo-500 focus:outline-none"
        value={bucket ?? ""}
        onChange={(e) => onBucketChange(e.target.value || undefined)}
      >
        <option value="">Auto</option>
        {filterOptions.buckets.map((b) => (
          <option key={b.value} value={b.value}>
            {b.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function AnalyticsToolbar({
  window,
  onWindowChange,
  since,
  until,
  previousSince,
  bucket,
  onBucketChange,
}: {
  window: AnalyticsWindow;
  onWindowChange: (w: AnalyticsWindow) => void;
  since?: string;
  until?: string;
  previousSince?: string;
  bucket?: string;
  onBucketChange?: (b: string | undefined) => void;
}) {
  const value: DateRange | null =
    window.mode === "custom" ? { start: window.start, end: window.end } : null;
  return (
    <div className="flex items-center justify-between w-full border-b border-slate-200">
      <div className="flex items-center">
        <TabBtn
          label="Last 7 Days"
          active={window.mode === "preset" && window.period === "week"}
          onClick={() => onWindowChange({ mode: "preset", period: "week" })}
        />
        <TabBtn
          label="Last 30 Days"
          active={window.mode === "preset" && window.period === "month"}
          onClick={() => onWindowChange({ mode: "preset", period: "month" })}
        />
        <div
          className={cn(
            "cursor-pointer px-3 py-2 text-sm font-medium -mb-px border-b-2 transition-colors",
            window.mode === "custom"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-slate-500 hover:text-slate-700",
          )}
        >
          <DateRangeSelector
            value={value}
            placeholder="Custom Range"
            presets={false}
            onChange={(r) =>
              r
                ? onWindowChange({ mode: "custom", start: r.start, end: r.end })
                : onWindowChange({ mode: "preset", period: "week" })
            }
          />
        </div>
      </div>
      <div className="flex items-center gap-4">
        {onBucketChange && (
          <GranularitySelect bucket={bucket} onBucketChange={onBucketChange} />
        )}
        <DateRangeDisplay
          since={since}
          until={until}
          previousSince={previousSince}
        />
      </div>
    </div>
  );
}
