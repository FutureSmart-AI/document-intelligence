import { map } from "lodash-es";
import {
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartTooltip } from "../components/ChartTooltip";
import { CustomXAxisTick } from "../components/CustomXAxisTick";
import { AXIS_LABEL_STYLE, AXIS_TICK_STYLE, GRID_PROPS } from "../constants";
import type { DataRecord, ResolvedAxes } from "../types";
import { computeXAxisLayout, formatValue } from "../utils";

interface MultiScatterChartProps {
  data: DataRecord[];
  xFieldName: string;
  yFieldName: string;
  /** Pre-pivoted series names (column keys). When provided, data is already pivoted. */
  series?: string[];
  colorsArray: string[];
  showLegend: boolean;
  axes: ResolvedAxes;
}

export const MultiScatterChart = ({
  data,
  xFieldName,
  yFieldName,
  series,
  colorsArray,
  showLegend,
  axes,
}: MultiScatterChartProps) => {
  let seriesNames: string[];
  let seriesData: DataRecord[][];

  if (series && series.length > 0) {
    // Data is already pivoted – split into per-series arrays for ScatterChart
    seriesNames = series;
    seriesData = series.map((name) =>
      data.map((item) => ({
        [xFieldName]: item[xFieldName],
        [yFieldName]: Number(item[name]) || 0,
      })),
    );
  } else {
    // Fallback: positional grouping
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

    seriesData = seriesNames.map((_, seriesIdx) =>
      categoryOrder
        .filter((key) => {
          const values = groupedMap.get(key)!;
          return seriesIdx < values.length;
        })
        .map((key) => {
          const values = groupedMap.get(key)!;
          return {
            [xFieldName]: key,
            [yFieldName]: values[seriesIdx] ?? 0,
          };
        }),
    );
  }

  const xLabels = data
    .map((d) => String(d[xFieldName]))
    .filter((v, i, a) => a.indexOf(v) === i);
  const xLayout = computeXAxisLayout(xLabels);

  return (
    <div style={{ width: "100%", minWidth: 0 }}>
      <ResponsiveContainer width="100%" height={300} minHeight={200}>
        <ScatterChart margin={{ top: 20, right: 28, left: 10, bottom: 30 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            dataKey={xFieldName}
            name={axes.x.label}
            type="category"
            allowDuplicatedCategory={false}
            tick={
              <CustomXAxisTick
                maxLabelLength={xLayout.maxLabelLength}
                angle={xLayout.angle}
                fontSize={xLayout.fontSize}
              />
            }
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
            dataKey={yFieldName}
            name={axes.y.label}
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
            cursor={{ strokeDasharray: "3 3", stroke: "#cbd5e1" }}
          />
          {showLegend && (
            <Legend
              iconType="circle"
              iconSize={8}
              wrapperStyle={{ fontSize: 12, paddingTop: 12 }}
            />
          )}
          {map(seriesNames, (name, idx) => (
            <Scatter
              key={name}
              data={seriesData[idx]}
              fill={colorsArray[idx % colorsArray.length]}
              fillOpacity={0.7}
              name={name}
              animationDuration={600}
              animationEasing="ease-out"
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};
