"use client";

import { type ReactNode } from "react";

import { cn } from "@/platform/utils";

import { SERIES } from "./constants";

/**
 * The Usage screen's own furniture, drawn from the design artifact.
 *
 * These live here rather than in `components/ui.tsx` because nothing else in
 * the product uses them: a segmented period control, a KPI tile with a
 * sparkline and a change chevron, a bar chart, and the bordered table shell
 * with a heading strip and a totals footer. All in theme tokens, so they follow
 * the light/dark toggle like the rest of the kit.
 */

const nfmt = new Intl.NumberFormat("en-US");

/** `12,400` — the exact figure, for tooltips and footers. */
export function comma(value: number): string {
  return nfmt.format(Math.round(Number(value) || 0));
}

/** `12.4K` — the artifact's headline abbreviation. */
export function compact(value: number): string {
  const n = Number(value) || 0;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e4) return `${(n / 1e3).toFixed(1)}K`;
  if (Math.abs(n) >= 1000) return nfmt.format(n);
  return String(Math.round(n));
}

/**
 * Cost, to as many places as it takes to not read as free.
 *
 * A real account's window can total a fraction of a cent, and `$0.00` beside a
 * thousand requests looks like a bug rather than a small bill.
 */
export function money(value: number): string {
  const n = Number(value) || 0;
  if (n > 0 && n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

/* ------------------------------------------------------------- period bar */

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: ReadonlyArray<{ id: T; label: string }>;
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex overflow-hidden rounded-di-md border border-line-strong"
    >
      {options.map((option) => {
        const on = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(option.id)}
            className={cn(
              "cursor-pointer border-0 border-r border-line px-3 py-[7px] text-[12.5px] font-semibold transition-colors last:border-r-0",
              on
                ? "bg-accent-soft text-accent-softtext"
                : "bg-surface text-ink-2 hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The Usage screen's own left rail.
 *
 * The artifact navigates this screen with a rail rather than a tab strip: the
 * three sections are places, and the period control below the heading applies to
 * all of them, which a row of tabs sitting above it would contradict. On a
 * narrow viewport it lies down into a scrollable row — a 196px column beside a
 * table on a phone leaves neither of them readable.
 */
export function Rail<T extends string>({
  items,
  value,
  onChange,
  label,
  heading,
  back,
}: {
  items: ReadonlyArray<{ id: T; label: string; icon: ReactNode }>;
  value: T;
  onChange: (id: T) => void;
  label: string;
  /** The rail's own title, above the items. */
  heading: ReactNode;
  /** The way out of the screen, at the top of the rail. */
  back?: ReactNode;
}) {
  return (
    <nav
      aria-label={label}
      className="mb-4 flex shrink-0 flex-col gap-1 lg:sticky lg:top-4 lg:mb-0 lg:w-[196px]"
    >
      {back}
      <div className="mt-1 mb-1 hidden items-center gap-2 px-2 lg:flex">
        {heading}
      </div>
      <div className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
        {items.map((item) => {
          const on = item.id === value;
          return (
            <button
              key={item.id}
              type="button"
              aria-current={on ? "page" : undefined}
              onClick={() => onChange(item.id)}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-di-md border-0 px-2.5 py-2 text-left text-[13px] font-semibold whitespace-nowrap transition-colors",
                on
                  ? "bg-accent-soft text-accent-softtext"
                  : "bg-transparent text-ink-2 hover:bg-sunken hover:text-ink",
              )}
            >
              <span aria-hidden="true" className="flex shrink-0 items-center">
                {item.icon}
              </span>
              {item.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}

/* ------------------------------------------------------------------- KPIs */

/**
 * A seven-point line over the tile's own series.
 *
 * Purely decorative — the figure above it is the number that matters — so it is
 * hidden from assistive technology rather than described twice.
 */
function Sparkline({ values }: { values: number[] }) {
  const series = values.length ? values : [0];
  const width = 72;
  const height = 22;
  const max = Math.max(...series, 1);
  const min = Math.min(...series);
  const x = (i: number) =>
    series.length < 2
      ? (width - 3) / 2 + 1.5
      : (i / (series.length - 1)) * (width - 3) + 1.5;
  const y = (v: number) =>
    height - 2 - ((v - min) / (max - min || 1)) * (height - 6);
  const d = series
    .map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
    .join("");

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      aria-hidden="true"
      className="block overflow-visible"
    >
      <path
        d={d}
        fill="none"
        className="stroke-ink-3 opacity-55"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle
        cx={x(series.length - 1).toFixed(1)}
        cy={y(series[series.length - 1]).toFixed(1)}
        r={2.4}
        className="fill-accent"
      />
    </svg>
  );
}

export function KpiGrid({ children }: { children: ReactNode }) {
  return (
    <div className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(210px,1fr))] gap-3">
      {children}
    </div>
  );
}

export function Kpi({
  label,
  value,
  /** What the service billed for this figure. Omitted where it bills nothing. */
  cost,
  /** Percent change against the previous window; `null` when incomparable. */
  delta,
  series,
  foot,
  loading,
}: {
  label: string;
  value: string;
  cost?: number | null;
  delta?: number | null;
  series?: number[];
  foot: string;
  loading?: boolean;
}) {
  const up = (delta ?? 0) >= 0;
  return (
    <div className="flex flex-col gap-1 rounded-di-lg border border-line bg-surface px-[15px] py-[13px] shadow-card">
      <span className="text-[11px] font-bold tracking-[0.05em] text-accent uppercase">
        {label}
      </span>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="di-display text-[22px] leading-none font-extrabold tracking-[-0.01em] text-ink tabular-nums">
          {loading ? "—" : value}
        </span>
        {/* The artifact hangs the money on the figure it was spent on, rather
            than in a column of its own — a cost is only ever read against the
            number that ran it up. */}
        {!loading && cost !== null && cost !== undefined && (
          <em className="text-[12.5px] font-bold text-success-text not-italic tabular-nums">
            {money(cost)}
          </em>
        )}
        {!loading && delta !== null && delta !== undefined && (
          <span
            className={cn(
              "inline-flex items-center gap-0.5 rounded-full px-[7px] py-px text-[11px] font-bold",
              up
                ? "bg-success-bg text-success-text"
                : "bg-danger-bg text-danger-text",
            )}
          >
            {up ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}%
          </span>
        )}
      </div>
      {series && series.length > 1 && <Sparkline values={series} />}
      <span className="mt-auto text-[11.5px] text-ink-2">{foot}</span>
    </div>
  );
}

/* --------------------------------------------------------- metric groups */

export interface Metric {
  key: string;
  label: string;
  /** `null` where the service reported nothing for this account at all. */
  value: number | null;
  /** The unit, for the tile's foot: "files", "pages", "characters". */
  unit: string;
  cost?: number | null;
  format?: (value: number) => string;
}

/**
 * A named group of secondary figures — Documents, Web, Making documents
 * searchable.
 *
 * The artifact's null rule is the whole point of the shape: a metric the
 * service did not report is left out, and a group whose every metric is missing
 * loses its heading too. A zero drawn where the service said nothing reads as
 * "you did none of this", which is a different claim from "this account does
 * not do this at all".
 */
export function MetricGroups({
  groups,
  foot,
  loading,
}: {
  groups: ReadonlyArray<{ heading: string; metrics: Metric[] }>;
  /** The period, repeated under each figure as the artifact repeats it. */
  foot: string;
  loading?: boolean;
}) {
  const live = groups
    .map((group) => ({
      ...group,
      metrics: group.metrics.filter(
        (metric) => metric.value !== null && metric.value !== undefined,
      ),
    }))
    .filter((group) => group.metrics.length);

  if (!live.length) return null;

  return (
    <div className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] items-start gap-3">
      {live.map((group) => (
        <div
          key={group.heading}
          className="rounded-di-lg border border-line bg-sunken p-3"
        >
          <div className="mb-2 px-1 text-[11px] font-bold tracking-[0.05em] text-ink-3 uppercase">
            {group.heading}
          </div>
          <div className="flex flex-col gap-2.5">
            {group.metrics.map((metric) => (
              <div
                key={metric.key}
                className="flex flex-col gap-1 rounded-di-md border border-line bg-surface px-[13px] py-2.5"
              >
                <span className="text-[11px] font-bold tracking-[0.05em] text-accent uppercase">
                  {metric.label}
                </span>
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="di-display text-[19px] leading-none font-extrabold text-ink tabular-nums">
                    {loading
                      ? "—"
                      : (metric.format ?? compact)(metric.value as number)}
                  </span>
                  {!loading &&
                    metric.cost !== null &&
                    metric.cost !== undefined && (
                      <em className="text-[12px] font-bold text-success-text not-italic tabular-nums">
                        {money(metric.cost)}
                      </em>
                    )}
                </div>
                <span className="text-[11.5px] text-ink-2">
                  {metric.unit} · {foot}
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ table */

export function TableShell({
  title,
  note,
  action,
  footer,
  children,
}: {
  title: string;
  note?: string;
  /** Controls that belong to this card, right-aligned in its heading strip. */
  action?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mb-4 overflow-hidden rounded-di-lg border border-line bg-surface shadow-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-[15px] py-3">
        {/* Title over its own line of explanation, as the artifact sets them —
            side by side the two read as one long heading. */}
        <span className="flex min-w-0 flex-col gap-0.5">
          <b className="di-display text-[13.5px] font-bold text-ink">{title}</b>
          {note && <span className="text-[12px] text-ink-2">{note}</span>}
        </span>
        {action && (
          <span className="ml-auto flex items-center gap-2">{action}</span>
        )}
      </div>
      {children}
      {footer && (
        <div className="flex flex-wrap items-center justify-between gap-2.5 border-t border-line px-3.5 py-2.5 text-[12.5px] text-ink-2">
          {footer}
        </div>
      )}
    </div>
  );
}

export type SortDirection = "asc" | "desc";
export interface SortState<K extends string> {
  key: K;
  dir: SortDirection;
}

/**
 * A sortable column heading — ascending, then descending, then unsorted, the
 * three-state cycle the artifact's table uses.
 */
export function SortableTh<K extends string>({
  columnKey,
  label,
  sort,
  onSort,
  numeric,
}: {
  columnKey: K;
  label: string;
  sort: SortState<K> | null;
  onSort: (key: K) => void;
  numeric?: boolean;
}) {
  const on = sort?.key === columnKey;
  return (
    <th
      className={cn("p-0 font-semibold", numeric ? "text-right" : "text-left")}
      aria-sort={
        on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"
      }
    >
      <button
        type="button"
        onClick={() => onSort(columnKey)}
        className={cn(
          "flex w-full cursor-pointer items-center gap-1 border-0 bg-transparent p-3 text-[13px] font-semibold hover:text-ink",
          numeric ? "justify-end" : "justify-start",
          on ? "text-ink" : "text-ink-2",
        )}
      >
        {label}
        {/* Idle columns keep a faint ↕ rather than nothing: a sort indicator
            that only appears once you have already sorted tells you what you
            just did, not what you can do. */}
        <span
          aria-hidden="true"
          className={cn("text-[9px]", on ? "opacity-100" : "opacity-40")}
        >
          {on ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
}

/** Cycles one column through asc → desc → unsorted. */
export function nextSort<K extends string>(
  current: SortState<K> | null,
  key: K,
): SortState<K> | null {
  if (!current || current.key !== key) return { key, dir: "asc" };
  if (current.dir === "asc") return { key, dir: "desc" };
  return null;
}

/** Sorts a copy — the caller's row order is never mutated. */
export function sortRows<K extends string, R extends Record<K, unknown>>(
  rows: R[],
  sort: SortState<K> | null,
): R[] {
  if (!sort) return rows;
  return [...rows].sort((a, b) => {
    const x = a[sort.key];
    const y = b[sort.key];
    if (typeof x === "string" && typeof y === "string") {
      return sort.dir === "asc" ? x.localeCompare(y) : y.localeCompare(x);
    }
    const nx = Number(x) || 0;
    const ny = Number(y) || 0;
    return sort.dir === "asc" ? nx - ny : ny - nx;
  });
}

/* ------------------------------------------------------------- quota bars */

/**
 * One limit and the counter it governs.
 *
 * A `null` limit is unlimited on this account, which the service means
 * literally — so the bar is left empty and labelled rather than drawn full or
 * drawn at zero, either of which would read as a quota.
 */
export function QuotaBar({
  label,
  used,
  limit,
  loading,
}: {
  label: string;
  used: number;
  limit: number | null | undefined;
  loading?: boolean;
}) {
  const unlimited = limit === null || limit === undefined;
  const pct = unlimited
    ? 0
    : Math.min(100, Math.round((used / (limit || 1)) * 100));
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-[12px]">
        <span className="font-semibold text-ink-2">{label}</span>
        <span className="text-ink-3 tabular-nums">
          {loading ? "—" : comma(used)} of{" "}
          {unlimited ? "Unlimited" : comma(limit)}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full border border-line-soft bg-sunken">
        <span
          className={cn(
            "block h-full rounded-full transition-[width] duration-300",
            pct >= 80 ? "bg-warn-dot" : "bg-accent",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-1 text-[11px] text-ink-3">
        {unlimited ? "No limit on this account" : `${pct}% used`}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- line chart */

export interface TrendPoint {
  label: string;
  value: number;
}

/**
 * The artifact's trend line: a value axis with four gridlines, the series as a
 * 2px stroke over a faint area, and a dot per bucket carrying the exact figure.
 *
 * One series, so there is no legend — the card's own heading names what is
 * plotted. Only the first and last bucket are labelled along the bottom: a
 * ninety-day window has thirteen buckets, and thirteen dates under a card this
 * wide collide into a grey smear.
 */
export function LineChart({
  points,
  formatValue = compact,
  label,
}: {
  points: TrendPoint[];
  formatValue?: (value: number) => string;
  label: string;
}) {
  const width = 720;
  const height = 240;
  const left = 58;
  const right = 12;
  const top = 12;
  const bottom = 30;
  const values = points.map((point) => point.value);
  const ceiling = Math.max(...values, 1) * 1.12;
  const x = (index: number) =>
    points.length < 2
      ? left + (width - left - right) / 2
      : left + (index / (points.length - 1)) * (width - left - right);
  const y = (value: number) =>
    top + (1 - value / ceiling) * (height - top - bottom);

  const path = points
    .map((point, index) => `${index ? "L" : "M"}${x(index)} ${y(point.value)}`)
    .join(" ");
  const area = `${path} L${x(points.length - 1)} ${y(0)} L${x(0)} ${y(0)} Z`;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      height={height}
      role="img"
      aria-label={label}
      preserveAspectRatio="none"
      className="block"
    >
      {[0, 1, 2, 3, 4].map((step) => {
        const value = (ceiling * step) / 4;
        return (
          <g key={step}>
            <line
              x1={left}
              y1={y(value)}
              x2={width - right}
              y2={y(value)}
              className="stroke-line"
              strokeWidth={1}
            />
            <text
              x={left - 8}
              y={y(value) + 3}
              textAnchor="end"
              className="fill-ink-3 text-[10px]"
            >
              {formatValue(value)}
            </text>
          </g>
        );
      })}

      <line
        x1={left}
        y1={top}
        x2={left}
        y2={height - bottom}
        className="stroke-line-strong"
        strokeWidth={1}
      />
      <line
        x1={left}
        y1={height - bottom}
        x2={width - right}
        y2={height - bottom}
        className="stroke-line-strong"
        strokeWidth={1}
      />

      <path d={area} className="fill-accent opacity-10" />
      <path
        d={path}
        fill="none"
        className="stroke-accent"
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {points.map((point, index) => (
        <circle
          key={`${point.label}-${index}`}
          cx={x(index)}
          cy={y(point.value)}
          r={4}
          className="fill-accent"
        >
          <title>{`${formatValue(point.value)} · ${point.label}`}</title>
        </circle>
      ))}

      <text x={left} y={height - 8} className="fill-ink-3 text-[10px]">
        {points[0]?.label ?? ""}
      </text>
      {points.length > 1 && (
        <text
          x={width - right}
          y={height - 8}
          textAnchor="end"
          className="fill-ink-3 text-[10px]"
        >
          {points[points.length - 1].label}
        </text>
      )}
    </svg>
  );
}

/* ------------------------------------------------------------------ donut */

export interface DonutSlice {
  label: string;
  value: number;
}

/**
 * Share of a total, as the artifact's ring with its own legend.
 *
 * Anything past the fourth slice is folded into "Other" rather than given a
 * generated hue: four is how many the palette was validated for, and a fifth
 * colour picked at render time is the one nobody checked against the rest.
 */
export function Donut({
  slices,
  formatValue,
  label,
  showEmpty,
}: {
  slices: DonutSlice[];
  formatValue: (value: number) => string;
  label: string;
  /**
   * List the slices measuring zero under the ring instead of dropping them.
   *
   * A slice worth nothing cannot be drawn — it has no arc — but on a ring of
   * costs that is exactly the fact worth reading: the operation ran and was
   * billed nothing. Dropping it reads as "this operation never happened".
   */
  showEmpty?: boolean;
}) {
  const ranked = [...slices]
    .filter((slice) => slice.value > 0)
    .sort((a, b) => b.value - a.value);
  const empty = showEmpty ? slices.filter((slice) => slice.value <= 0) : [];
  const shown = ranked.slice(0, 4);
  const rest = ranked.slice(4);
  if (rest.length) {
    shown.push({
      label: "Other",
      value: rest.reduce((sum, slice) => sum + slice.value, 0),
    });
  }
  const total = shown.reduce((sum, slice) => sum + slice.value, 0);
  const circumference = 2 * Math.PI * 54;
  let offset = 0;

  return (
    <div className="flex flex-wrap items-center justify-center gap-5 p-4">
      <svg
        viewBox="0 0 140 140"
        width={150}
        height={150}
        role="img"
        aria-label={label}
      >
        {shown.map((slice, index) => {
          const length = (circumference * slice.value) / total;
          const dash = `${length} ${circumference - length}`;
          const element = (
            <circle
              key={slice.label}
              cx={70}
              cy={70}
              r={54}
              fill="none"
              stroke={SERIES[index % SERIES.length]}
              strokeWidth={18}
              strokeDasharray={dash}
              strokeDashoffset={-offset}
              transform="rotate(-90 70 70)"
            >
              <title>{`${slice.label} · ${Math.round((slice.value / total) * 100)}%`}</title>
            </circle>
          );
          offset += length;
          return element;
        })}
      </svg>
      <ul className="m-0 min-w-[190px] flex-1 list-none space-y-2 p-0">
        {shown.map((slice, index) => (
          <li
            key={slice.label}
            className="flex items-center gap-2 text-[12.5px] text-ink-2"
          >
            <span
              aria-hidden="true"
              className="size-2.5 shrink-0 rounded-full"
              style={{ background: SERIES[index % SERIES.length] }}
            />
            <span className="min-w-0 truncate">{slice.label}</span>
            <span className="ml-auto font-bold text-ink tabular-nums">
              {Math.round((slice.value / total) * 100)}%
            </span>
            <span className="w-20 text-right text-ink-3 tabular-nums">
              {formatValue(slice.value)}
            </span>
          </li>
        ))}
        {empty.map((slice) => (
          <li
            key={slice.label}
            className="flex items-center gap-2 text-[12.5px] text-ink-3"
          >
            <span
              aria-hidden="true"
              className="size-2.5 shrink-0 rounded-full border border-line-strong"
            />
            <span className="min-w-0 truncate">{slice.label}</span>
            <span className="ml-auto font-bold tabular-nums">0%</span>
            <span className="w-20 text-right tabular-nums">
              {formatValue(0)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
