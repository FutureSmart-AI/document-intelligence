"use client";

import { MAX_BARS, SERIES_COLORS } from "./constants";
import type { Histogram, VizItem } from "./facets";
import { formatStat } from "./format";

/**
 * The four views over the table — bars, share, distribution and coverage.
 *
 * All four read the rows the grid is showing, so a chart can never disagree
 * with the table under it. They are drawn as markup rather than pulled from a
 * chart library on purpose: these are small, fixed shapes, and the series
 * colours come from the product's own tokens so they follow the theme toggle.
 */

export function VizNote({ children }: { children: React.ReactNode }) {
  return <p className="mb-2.5 text-[12px] text-ink-2">{children}</p>;
}

export function Bars({
  items,
  total,
}: {
  items: VizItem[];
  /** The denominator for the percentages — documents in view, not the bar sum. */
  total: number;
}) {
  if (!items.length)
    return (
      <p className="text-[12.5px] text-ink-3">
        Nothing to show for this field yet.
      </p>
    );
  const max = items.reduce((peak, item) => Math.max(peak, item.count), 1);
  const shown = items.slice(0, MAX_BARS);

  return (
    <>
      <div className="flex flex-col gap-[7px]">
        {shown.map((item) => (
          <div
            key={item.label}
            className="grid items-center gap-2.5"
            style={{
              gridTemplateColumns: "minmax(90px,180px) 1fr 40px 42px",
            }}
          >
            <span
              className="truncate text-[12.5px] text-ink"
              title={item.label}
            >
              {item.label}
            </span>
            <span className="h-1.5 w-full overflow-hidden rounded-full bg-sunken">
              <span
                className="block h-full rounded-full bg-accent"
                style={{ width: `${Math.round((item.count / max) * 100)}%` }}
              />
            </span>
            <span className="tnum text-right text-[12.5px] font-semibold text-ink">
              {item.count}
            </span>
            <span className="tnum hidden text-right text-[11.5px] text-ink-3 sm:block">
              {total ? Math.round((item.count / total) * 100) : 0}%
            </span>
          </div>
        ))}
      </div>
      {items.length > MAX_BARS && (
        <p className="mt-2.5 text-[12px] text-ink-2">
          Showing the {MAX_BARS} largest of {items.length} values. Open
          &ldquo;See all values&rdquo; for the rest.
        </p>
      )}
    </>
  );
}

/** One dash-offset arc per segment, the same technique as the analytics donut. */
export function Donut({
  items,
  total,
  centerLabel,
}: {
  items: VizItem[];
  total: number;
  centerLabel: string;
}) {
  const circumference = 2 * Math.PI * 38;
  let offset = 0;
  const arcs = items.map((item, index) => {
    const length = total ? (item.count / total) * circumference : 0;
    const arc = (
      <circle
        key={item.label}
        cx="50"
        cy="50"
        r="38"
        fill="none"
        stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
        strokeWidth="16"
        strokeDasharray={`${length.toFixed(2)} ${(circumference - length).toFixed(2)}`}
        strokeDashoffset={(-offset).toFixed(2)}
        transform="rotate(-90 50 50)"
      >
        <title>{`${item.label}: ${item.count}`}</title>
      </circle>
    );
    offset += length;
    return arc;
  });

  return (
    <div className="relative flex flex-wrap items-center gap-6">
      <div className="relative size-[150px] flex-none">
        <svg
          viewBox="0 0 100 100"
          className="size-[150px]"
          role="img"
          aria-label="Share of documents by value"
        >
          <circle
            cx="50"
            cy="50"
            r="38"
            fill="none"
            stroke="var(--di-sunken)"
            strokeWidth="16"
          />
          {arcs}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <b className="tnum text-[22px] leading-none text-ink">{total}</b>
          <span className="text-[11px] text-ink-3">{centerLabel}</span>
        </div>
      </div>
      {/* Capped: as a plain flex-1 the legend stretched the whole panel width
          and left its counts a screen away from their labels. */}
      <div className="flex min-w-55 max-w-lg flex-1 flex-col gap-1.25">
        {items.map((item, index) => (
          <div
            key={item.label}
            className="grid items-center gap-2.25 text-[12.5px]"
            style={{ gridTemplateColumns: "12px 1fr 40px 42px" }}
          >
            <span
              className="size-2.5 rounded-[3px]"
              style={{
                background: SERIES_COLORS[index % SERIES_COLORS.length],
              }}
            />
            <span className="truncate text-ink" title={item.label}>
              {item.label}
            </span>
            <span className="tnum text-right font-semibold text-ink">
              {item.count}
            </span>
            <span className="tnum text-right text-[11.5px] text-ink-3">
              {total ? Math.round((item.count / total) * 100) : 0}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function StatCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-21.5 flex-col gap-px rounded-di-sm border border-line-soft bg-sunken px-3 py-2">
      <span className="text-[11px] text-ink-3">{label}</span>
      <b className="tnum text-[15px] text-ink">{value}</b>
    </div>
  );
}

export function Distribution({
  histogram,
  label,
}: {
  histogram: Histogram | null;
  label: string;
}) {
  if (!histogram)
    return (
      <p className="text-[12.5px] text-ink-3">
        No document has a number in {label} yet.
      </p>
    );
  return (
    <>
      <div className="mb-3.5 flex flex-wrap gap-2">
        <StatCell label="Lowest" value={formatStat(histogram.min)} />
        <StatCell label="Median" value={formatStat(histogram.median)} />
        <StatCell label="Average" value={formatStat(histogram.mean)} />
        <StatCell label="Highest" value={formatStat(histogram.max)} />
        <StatCell label="Total" value={formatStat(histogram.sum)} />
        <StatCell label="Documents" value={String(histogram.count)} />
      </div>
      <Bars items={histogram.buckets} total={histogram.count} />
    </>
  );
}

export function Coverage({
  items,
  rowCount,
}: {
  items: VizItem[];
  rowCount: number;
}) {
  if (!rowCount)
    return (
      <p className="text-[12.5px] text-ink-3">
        No documents in view, so coverage has nothing to measure.
      </p>
    );
  if (!items.length)
    return (
      <p className="text-[12.5px] text-ink-3">
        No columns in view. Turn a group back on with Show, above.
      </p>
    );
  const thinnest = items[0];
  return (
    <>
      <VizNote>
        How many of these {rowCount} {rowCount === 1 ? "document" : "documents"}{" "}
        actually have a value in each column.{" "}
        {thinnest && thinnest.count < rowCount ? (
          <>
            <b className="text-ink">{thinnest.label}</b> is the thinnest at{" "}
            {thinnest.count} of {rowCount}.
          </>
        ) : (
          <>Every column has a value in every document.</>
        )}
      </VizNote>
      <Bars items={items} total={rowCount} />
    </>
  );
}
