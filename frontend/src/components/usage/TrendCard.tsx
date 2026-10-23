"use client";

import { useState } from "react";

import { Select } from "@/components/ui";
import {
  useAnalytics,
  type AggregatedMetrics,
  type AnalyticsFilters,
  type DateRange,
  type TimeBucket,
} from "@/data/usage";

import {
  TREND_GRANULARITIES,
  TREND_METRICS,
  type TrendMetricId,
} from "./constants";
import { comma, compact, LineChart, money, TableShell } from "./UsageBits";

/**
 * Usage Trends — one metric over the window, at the granularity you choose.
 *
 * The artifact's card, with its two controls. Which figure is plotted and how
 * finely it is bucketed are questions about this card alone, so they live here
 * rather than beside the period control, which every panel on the screen obeys.
 *
 * Granularity is a real query parameter, not a fold of the daily answer: the
 * service buckets by hour, day, week or month itself, and asking it for months
 * is one response of three points rather than ninety points added up here.
 */

type MetricId = TrendMetricId;

function valueOf(metrics: AggregatedMetrics, metric: MetricId): number {
  if (metric === "cost") return metrics.cost?.total_cost_usd ?? 0;
  if (metric === "pages") {
    return (
      (metrics.llama_parse_page_count ?? 0) +
      (metrics.processed_weblink_count ?? 0)
    );
  }
  if (metric === "request_count") return metrics.request_count ?? 0;
  return metrics[metric] ?? 0;
}

export default function TrendCard({
  range,
  filters,
  periodLabel,
  /** The bucket the chosen period reports in, until the reader picks another. */
  defaultBucket,
}: {
  range: DateRange;
  filters: AnalyticsFilters | undefined;
  periodLabel: string;
  defaultBucket: TimeBucket;
}) {
  const [metric, setMetric] = useState<MetricId>("total_tokens");
  /**
   * `null` until the reader picks a granularity, and then theirs for good.
   *
   * Following the period until then is the point: ninety days bucketed by day
   * is ninety points across a card this wide, which is why the period reports
   * in weeks at that length. Once someone has chosen, the choice stands — a
   * control that silently resets on the next period change is worse than one
   * that does not follow at all.
   */
  const [chosenBucket, setChosenBucket] = useState<TimeBucket | null>(null);
  const bucket = chosenBucket ?? defaultBucket;

  const trend = useAnalytics({
    date_range: range,
    time_bucket: bucket,
    group_by: [],
    filters,
  });

  const points = [...(trend.data?.data ?? [])]
    .map((point) => ({
      bucket: point.dimensions.time_bucket ?? point.dimensions.bucket ?? "",
      value: valueOf(point.metrics, metric),
    }))
    .filter((point) => point.bucket)
    .sort((a, b) => a.bucket.localeCompare(b.bucket))
    .map((point) => ({
      // `2026-08-19` reads as `08-19`; a month bucket keeps its month.
      label:
        bucket === "month"
          ? point.bucket.slice(0, 7)
          : point.bucket.slice(5, 10),
      value: point.value,
    }));

  const format = metric === "cost" ? money : compact;
  const total = points.reduce((sum, point) => sum + point.value, 0);
  const metricLabel =
    TREND_METRICS.find((entry) => entry.id === metric)?.label ?? "Total tokens";

  return (
    <TableShell
      title="Usage Trends"
      note="Monitor your consumption over time"
      action={
        <>
          <Select
            className="w-auto! min-w-37.5"
            aria-label="Trend metric"
            value={metric}
            onChange={(event) => setMetric(event.target.value as MetricId)}
          >
            {TREND_METRICS.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </Select>
          <Select
            className="w-auto! min-w-30"
            aria-label="Trend granularity"
            value={bucket}
            onChange={(event) =>
              setChosenBucket(event.target.value as TimeBucket)
            }
          >
            {TREND_GRANULARITIES.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </Select>
        </>
      }
      footer={
        points.length ? (
          <>
            <span>
              {metricLabel} · {periodLabel}
            </span>
            <span className="tabular-nums">
              {metric === "cost" ? money(total) : comma(total)} across{" "}
              {points.length} bucket{points.length === 1 ? "" : "s"}
            </span>
          </>
        ) : undefined
      }
    >
      <div className="px-3.75 py-4">
        {trend.isLoading ? (
          <p className="m-0 py-10 text-center text-[13px] text-ink-2">
            Reading…
          </p>
        ) : points.length < 2 ? (
          <p className="m-0 py-10 text-center text-[13px] text-ink-2">
            {points.length
              ? "One bucket in this window — choose a finer granularity, or a longer period, to see a trend."
              : "Nothing was recorded in this window."}
          </p>
        ) : (
          <LineChart
            points={points}
            formatValue={format}
            label={`${metricLabel} over ${periodLabel.toLowerCase()}`}
          />
        )}
      </div>
    </TableShell>
  );
}
