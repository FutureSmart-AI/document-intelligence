"use client";

import { includes, isArray, isEmpty, isNil, lowerCase } from "lodash-es";

import { DEFAULT_COLORS } from "./constants";
import { SimpleBarChart } from "./renderers/BarChart";
import { SimpleBubbleChart } from "./renderers/BubbleChart";
import { GroupedBarChart } from "./renderers/GroupedBarChart";
import { SimpleLineChart } from "./renderers/LineChart";
import { MultiBubbleChart } from "./renderers/MultiBubbleChart";
import { MultiLineChart } from "./renderers/MultiLineChart";
import { MultiScatterChart } from "./renderers/MultiScatterChart";
import { SimplePieChart } from "./renderers/PieChart";
import { SimpleScatterChart } from "./renderers/ScatterChart";
import { StackedBarChart } from "./renderers/StackedBarChart";
import type { ChatChartProps } from "./types";
import {
  detectGroupField,
  isIntegerSeries,
  normalizeData,
  pivotByGroup,
  resolveAxes,
  resolveSeriesKeys,
} from "./utils";

const ChartShell = ({
  className,
  title,
  children,
}: {
  className?: string;
  title?: string;
  children: React.ReactNode;
}) => (
  <div
    className={
      className ??
      "mt-3 rounded-xl border border-slate-200 bg-white p-3 sm:p-5 shadow-sm overflow-hidden w-full min-w-0"
    }
  >
    {title && (
      <div className="mb-3">
        <h3 className="text-sm sm:text-base font-semibold text-slate-900 tracking-tight">
          {title}
        </h3>
      </div>
    )}
    {children}
  </div>
);

const EmptyState = ({ message }: { message: string }) => (
  <div
    role="status"
    className="flex h-[200px] w-full items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50/50 text-sm text-slate-500"
  >
    {message}
  </div>
);

export const ChatChart = ({
  chartData,
  chartSuggestion,
  className,
}: ChatChartProps) => {
  // Early returns
  if (isNil(chartData) || isNil(chartSuggestion)) return null;

  const metadata = chartData.metadata ?? {};
  const axes = resolveAxes(metadata);
  const normalized = normalizeData(chartData.data, axes);

  const title = metadata.title || "Chart";

  if (!normalized || isEmpty(normalized.data)) {
    return (
      <ChartShell className={className} title={title}>
        <EmptyState message="No data available to render this chart." />
      </ChartShell>
    );
  }

  const { data, xField, yField, series } = normalized;
  const chartType = lowerCase(chartSuggestion ?? "");

  // Extract metadata values
  const pointColor: string =
    metadata.point?.color ?? DEFAULT_COLORS[0] ?? "#4facfe";
  const pointSize: number = metadata.point?.size ?? 8;
  const showLegend = metadata.legend?.show !== false;
  const chartOrientation = metadata.chart_orientation || "vertical";
  const colors = metadata.colors;

  // Resolve colors
  const colorsArray = isArray(colors) ? colors : DEFAULT_COLORS;
  const colorsObj =
    !isArray(colors) && typeof colors === "object" ? colors : {};

  // Get field names from first data item (in case they differ from metadata)
  const firstItem = data[0] || {};
  const xFieldName = String((firstItem as any)._xField || xField);
  const yFieldName = String((firstItem as any)._yField || yField);

  if (!xFieldName || !yFieldName) {
    return (
      <ChartShell className={className} title={title}>
        <EmptyState message="Chart data is missing axis fields." />
      </ChartShell>
    );
  }

  // Check if data has duplicate categories (used to auto-detect multi-series)
  const hasDuplicateCategories = (() => {
    const categories = data.map((d) => String(d[xFieldName]));
    return new Set(categories).size < categories.length;
  })();

  // ── Group field detection & data pivoting ─────────────────────────────
  // When data has a "group" field (e.g. {category:"Bengaluru", value:50, skill:"Python"}),
  // pivot it into columnar format so charts show real series names instead of "Series 1, 2, 3"
  const groupField =
    metadata.group?.field ??
    (hasDuplicateCategories
      ? detectGroupField(data, xFieldName, yFieldName)
      : null);

  const pivoted = groupField
    ? pivotByGroup(data, xFieldName, yFieldName, groupField)
    : null;

  // If we pivoted, use the pivoted data and series; otherwise fall through to original behavior
  const chartData_ = pivoted ? pivoted.data : data;

  // legend.items is a metadata-driven whitelist of *display labels*. Each
  // candidate is resolved against the actual data record keys (exact, case-
  // insensitive, or snake_cased). Candidates that don't resolve are dropped —
  // otherwise a chart like `{x, y}` + items:["INR","USD"] would silently render
  // nothing because the renderer would set dataKey="INR" on data with no INR key.
  const legendCandidates = isArray(metadata.legend?.items)
    ? metadata.legend.items
    : [];
  // Note: we intentionally do NOT exclude `yFieldName` here. When the data has
  // multiple numeric columns (e.g. order_count + total_revenue) `normalizeData`
  // picks one as the default yField — but it's still a legitimate whitelisted
  // series and must keep its place in the resolved list.
  const resolved = !pivoted
    ? resolveSeriesKeys(data, legendCandidates, xFieldName)
    : [];

  const seriesNames = pivoted
    ? pivoted.series
    : series.length > 0
      ? series
      : resolved.map((r) => r.key);
  const seriesLabels = pivoted
    ? pivoted.series
    : series.length > 0
      ? series
      : resolved.map((r) => r.label);

  // Detect integer-only Y data so the axis doesn't generate "0.8" / "1.5" ticks
  // for count values that happen to come over the wire as 3.0 / 2.0.
  const yFieldsForIntCheck =
    seriesNames.length > 0 ? seriesNames : [yFieldName];
  const integerY = isIntegerSeries(chartData_, yFieldsForIntCheck);

  const renderChart = () => {
    // ── Explicit grouped chart types ──────────────────────────────────
    if (
      includes(
        ["grouped bar chart", "grouped column chart", "grouped chart"],
        chartType,
      )
    ) {
      return (
        <GroupedBarChart
          data={chartData_}
          xFieldName={xFieldName}
          yFieldName={yFieldName}
          series={seriesNames}
          seriesLabels={seriesLabels}
          colorsArray={colorsArray}
          showLegend={showLegend}
          axes={axes}
        />
      );
    }

    // ── Stacked bar ───────────────────────────────────────────────────
    if (includes(["stacked bar chart", "stacked column chart"], chartType)) {
      // seriesNames already prefers normalized series over legend.items (see above)
      const stackSeries = seriesNames.length > 0 ? seriesNames : series;
      const stackData = seriesNames.length > 0 ? chartData_ : data;
      return (
        <StackedBarChart
          data={stackData}
          xFieldName={xFieldName}
          yFieldName={yFieldName}
          series={stackSeries}
          firstItem={firstItem}
          isHorizontal={chartOrientation === "horizontal"}
          showLegend={showLegend}
          colorsObj={colorsObj}
          colorsArray={colorsArray}
          axes={axes}
        />
      );
    }

    // ── Bar / Column ──────────────────────────────────────────────────
    if (includes(["bar chart", "column chart"], chartType)) {
      if (hasDuplicateCategories || seriesNames.length > 0) {
        return (
          <GroupedBarChart
            data={chartData_}
            xFieldName={xFieldName}
            yFieldName={yFieldName}
            series={seriesNames}
            seriesLabels={seriesLabels}
            colorsArray={colorsArray}
            showLegend={showLegend}
            axes={axes}
          />
        );
      }
      return (
        <SimpleBarChart
          data={data}
          xFieldName={xFieldName}
          yFieldName={yFieldName}
          colorsArray={colorsArray}
          showLegend={showLegend}
          axes={axes}
          integerY={integerY}
        />
      );
    }

    // ── Line ──────────────────────────────────────────────────────────
    if (chartType === "line chart") {
      if (hasDuplicateCategories || seriesNames.length > 0) {
        return (
          <MultiLineChart
            data={chartData_}
            xFieldName={xFieldName}
            yFieldName={yFieldName}
            series={seriesNames}
            seriesLabels={seriesLabels}
            colorsArray={colorsArray}
            showLegend={showLegend}
            axes={axes}
            integerY={integerY}
          />
        );
      }
      return (
        <SimpleLineChart
          data={data}
          xFieldName={xFieldName}
          yFieldName={yFieldName}
          pointColor={pointColor}
          pointSize={pointSize}
          showLegend={showLegend}
          metadata={metadata}
          axes={axes}
          integerY={integerY}
        />
      );
    }

    // ── Pie (aggregates duplicates internally) ────────────────────────
    if (chartType === "pie chart") {
      return (
        <SimplePieChart
          data={data}
          xFieldName={xFieldName}
          yFieldName={yFieldName}
          colorsArray={colorsArray}
          showLegend={showLegend}
        />
      );
    }

    // ── Bubble ────────────────────────────────────────────────────────
    if (chartType === "bubble chart") {
      if (hasDuplicateCategories || seriesNames.length > 0) {
        return (
          <MultiBubbleChart
            data={chartData_}
            xFieldName={xFieldName}
            yFieldName={yFieldName}
            firstItem={firstItem}
            series={seriesNames}
            pointSize={pointSize}
            colorsArray={colorsArray}
            showLegend={showLegend}
            axes={axes}
          />
        );
      }
      return (
        <SimpleBubbleChart
          data={data}
          xFieldName={xFieldName}
          yFieldName={yFieldName}
          firstItem={firstItem}
          pointColor={pointColor}
          pointSize={pointSize}
          showLegend={showLegend}
          colorsArray={colorsArray}
          axes={axes}
        />
      );
    }

    // ── Scatter ───────────────────────────────────────────────────────
    if (chartType === "scatter chart") {
      if (hasDuplicateCategories || seriesNames.length > 0) {
        return (
          <MultiScatterChart
            data={chartData_}
            xFieldName={xFieldName}
            yFieldName={yFieldName}
            series={seriesNames}
            colorsArray={colorsArray}
            showLegend={showLegend}
            axes={axes}
          />
        );
      }
      return (
        <SimpleScatterChart
          data={data}
          xFieldName={xFieldName}
          yFieldName={yFieldName}
          pointColor={pointColor}
          showLegend={showLegend}
          axes={axes}
        />
      );
    }

    // ── Default fallback (scatter) ────────────────────────────────────
    if (hasDuplicateCategories || seriesNames.length > 0) {
      return (
        <MultiScatterChart
          data={chartData_}
          xFieldName={xFieldName}
          yFieldName={yFieldName}
          series={seriesNames}
          colorsArray={colorsArray}
          showLegend={showLegend}
          axes={axes}
        />
      );
    }
    return (
      <SimpleScatterChart
        data={data}
        xFieldName={xFieldName}
        yFieldName={yFieldName}
        pointColor={pointColor}
        showLegend={showLegend}
        axes={axes}
      />
    );
  };

  let rendered: React.ReactNode;
  try {
    rendered = renderChart();
  } catch (err) {
    if (typeof console !== "undefined") {
      console.error("[ChatChart] failed to render", err);
    }
    rendered = (
      <EmptyState message="Chart could not be rendered due to an unexpected error." />
    );
  }

  return (
    <ChartShell className={className} title={title}>
      <div className="w-full min-w-0 overflow-x-auto">{rendered}</div>
    </ChartShell>
  );
};
