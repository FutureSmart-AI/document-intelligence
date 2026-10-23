import { map } from "lodash-es";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartTooltip } from "../components/ChartTooltip";
import { CustomXAxisTick } from "../components/CustomXAxisTick";
import {
  AXIS_LABEL_STYLE,
  AXIS_TICK_STYLE,
  CHART_MARGINS,
  GRID_PROPS,
} from "../constants";
import type { DataRecord, ResolvedAxes } from "../types";
import { computeXAxisLayout, formatValue } from "../utils";

interface GroupedBarChartProps {
  data: DataRecord[];
  xFieldName: string;
  yFieldName: string;
  /** Pre-pivoted series names (column keys). When provided, data is already pivoted. */
  series?: string[];
  /** Optional display labels parallel to `series`. Defaults to series keys. */
  seriesLabels?: string[];
  colorsArray: string[];
  showLegend: boolean;
  axes: ResolvedAxes;
}

export const GroupedBarChart = ({
  data,
  xFieldName,
  yFieldName,
  series,
  seriesLabels,
  colorsArray,
  showLegend,
  axes,
}: GroupedBarChartProps) => {
  let groupedData: DataRecord[];
  let seriesNames: string[] = series ?? [];

  // ChatChart now resolves legend.items → real data keys before reaching here,
  // so when `series` is provided we trust those keys exist on the records
  // verbatim — no positional pivoting needed.
  const dataHasSeriesKeys =
    !!series &&
    series.length > 0 &&
    data.length > 0 &&
    series.every((s) => s in data[0]);

  if (dataHasSeriesKeys) {
    groupedData = data;
    seriesNames = series!;
  } else if (series && series.length > 0) {
    // Pre-pivoted series names supplied but data isn't columnar — pivot
    // positionally as a fallback for callers that haven't migrated yet.
    const groupedMap = new Map<string, number[]>();
    const categoryOrder: string[] = [];

    data?.forEach((item) => {
      const key = String(item[xFieldName]);
      if (!groupedMap.has(key)) {
        groupedMap.set(key, []);
        categoryOrder.push(key);
      }
      groupedMap.get(key)?.push(Number(item[yFieldName]) || 0);
    });

    groupedData = map(categoryOrder, (key) => {
      const values = groupedMap.get(key)!;
      const record: DataRecord = { [xFieldName]: key };
      series?.forEach((name, idx) => {
        record[name] = values[idx] ?? 0;
      });
      return record;
    });
  } else {
    // Fallback: positional grouping for data without a group field
    const groupedMap = new Map<string, number[]>();
    const categoryOrder: string[] = [];

    data?.forEach((item) => {
      const key = String(item[xFieldName]);
      if (!groupedMap.has(key)) {
        groupedMap.set(key, []);
        categoryOrder.push(key);
      }
      groupedMap.get(key)!.push(Number(item[yFieldName]) || 0);
    });

    let seriesCount = 0;
    groupedMap.forEach((values) => {
      seriesCount = Math.max(seriesCount, values.length);
    });

    seriesNames = Array.from(
      { length: seriesCount },
      (_, i) => `Series ${i + 1}`,
    );

    groupedData = map(categoryOrder, (key) => {
      const values = groupedMap.get(key)!;
      const record: DataRecord = { [xFieldName]: key };
      seriesNames.forEach((name, idx) => {
        record[name] = values[idx] ?? 0;
      });
      return record;
    });
  }

  const xLabels = map(groupedData, (d) => d[xFieldName]);
  const xLayout = computeXAxisLayout(xLabels);
  const chartMargins = { ...CHART_MARGINS, bottom: 30, left: 10 };

  return (
    <div style={{ width: "100%", minWidth: 0 }}>
      <ResponsiveContainer width="100%" height={300} minHeight={200}>
        <BarChart data={groupedData} margin={chartMargins} barGap={2}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            dataKey={xFieldName}
            tick={
              <CustomXAxisTick
                maxLabelLength={xLayout.maxLabelLength}
                angle={xLayout.angle}
                fontSize={xLayout.fontSize}
              />
            }
            interval={0}
            height={xLayout.height}
            axisLine={false}
            tickLine={false}
            label={{
              value: axes.x.label,
              position: "insideBottom",
              style: AXIS_LABEL_STYLE,
              offset: 0,
            }}
          />
          <YAxis
            tick={AXIS_TICK_STYLE}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
            tickFormatter={(v) => formatValue(v)}
            label={{
              value: axes.y.label,
              angle: -90,
              position: "insideLeft",
              style: { ...AXIS_LABEL_STYLE, textAnchor: "middle" },
            }}
          />
          <Tooltip
            content={<ChartTooltip />}
            cursor={{ fill: "rgba(99, 102, 241, 0.04)", radius: 4 }}
          />
          {showLegend && (
            <Legend
              iconType="circle"
              iconSize={8}
              wrapperStyle={{ fontSize: 12, paddingTop: 12 }}
            />
          )}
          {map(seriesNames, (name, idx) => (
            <Bar
              key={name}
              dataKey={name}
              fill={colorsArray[idx % colorsArray.length]}
              radius={[6, 6, 0, 0]}
              name={seriesLabels?.[idx] ?? name}
              animationDuration={600}
              animationEasing="ease-out"
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};
