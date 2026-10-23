import { map } from "lodash-es";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
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

interface MultiLineChartProps {
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
  integerY?: boolean;
}

export const MultiLineChart = ({
  data,
  xFieldName,
  yFieldName,
  series,
  seriesLabels,
  colorsArray,
  showLegend,
  axes,
  integerY,
}: MultiLineChartProps) => {
  let groupedData: DataRecord[];
  let seriesNames: string[];
  let labels: string[];

  if (series && series.length > 0) {
    // Data is already pivoted by ChatChart – use as-is
    groupedData = data;
    seriesNames = series;
    labels =
      seriesLabels && seriesLabels.length === series.length
        ? seriesLabels
        : series;
  } else {
    // Fallback: positional grouping for data without a group field
    const groupedMap = new Map<string, number[]>();
    const categoryOrder: string[] = [];

    data.forEach((item) => {
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
    labels = seriesNames;

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
        <LineChart data={groupedData} margin={chartMargins}>
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
              offset: 0,
              style: AXIS_LABEL_STYLE,
            }}
          />
          <YAxis
            tick={AXIS_TICK_STYLE}
            axisLine={false}
            tickLine={false}
            allowDecimals={integerY === false}
            tickFormatter={(v) => formatValue(v)}
            label={{
              value: axes.y.label,
              angle: -90,
              position: "insideLeft",
              style: { ...AXIS_LABEL_STYLE, textAnchor: "middle" },
            }}
          />
          <Tooltip content={<ChartTooltip />} />
          {showLegend && (
            <Legend
              iconType="circle"
              iconSize={8}
              wrapperStyle={{ fontSize: 12, paddingTop: 12 }}
            />
          )}
          {map(seriesNames, (name, idx) => {
            const color = colorsArray[idx % colorsArray.length];
            const displayName = labels[idx] ?? name;
            return (
              <Line
                key={name}
                type="monotone"
                dataKey={name}
                stroke={color}
                strokeWidth={2.5}
                dot={{ r: 4, fill: color, strokeWidth: 2, stroke: "white" }}
                activeDot={{
                  r: 6,
                  strokeWidth: 2,
                  stroke: "white",
                  fill: color,
                }}
                name={displayName}
                animationDuration={600}
                animationEasing="ease-out"
              />
            );
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};
