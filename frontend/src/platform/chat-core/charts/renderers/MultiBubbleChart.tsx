import { find, lowerCase, map } from "lodash-es";
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

interface MultiBubbleChartProps {
  data: DataRecord[];
  xFieldName: string;
  yFieldName: string;
  firstItem: DataRecord;
  /** Pre-pivoted series names (column keys). When provided, data is already pivoted. */
  series?: string[];
  pointSize: number;
  colorsArray: string[];
  showLegend: boolean;
  axes: ResolvedAxes;
}

export const MultiBubbleChart = ({
  data,
  xFieldName,
  yFieldName,
  firstItem,
  series,
  pointSize,
  colorsArray,
  showLegend,
  axes,
}: MultiBubbleChartProps) => {
  // Detect size field
  const dataKeys = Object.keys(firstItem);
  const sizeField =
    find(dataKeys, (k) => lowerCase(k) === "size") ||
    find(dataKeys, (k) => lowerCase(k) === "z") ||
    yFieldName;

  let seriesNames: string[];
  let seriesData: DataRecord[][];

  // Helper to normalize bubble sizes
  const normalizeBubbleSize = (raw: number, min: number, max: number) => {
    if (min === max) return 50;
    return 20 + ((raw - min) / (max - min || 1)) * 80;
  };

  if (series && series.length > 0) {
    // Data is already pivoted – split into per-series arrays for ScatterChart
    seriesNames = series;

    // Collect all values to compute size normalization
    const allValues: number[] = [];
    data.forEach((item) => {
      series.forEach((name) => {
        allValues.push(Number(item[name]) || 0);
      });
    });
    const minSize = Math.min(...allValues);
    const maxSize = Math.max(...allValues);

    seriesData = series.map((name) =>
      data.map((item) => {
        const val = Number(item[name]) || 0;
        return {
          [xFieldName]: item[xFieldName],
          [yFieldName]: val,
          z: normalizeBubbleSize(val, minSize, maxSize),
        };
      }),
    );
  } else {
    // Fallback: positional grouping
    const groupedMap = new Map<string, { y: number; size: number }[]>();
    const categoryOrder: string[] = [];

    data.forEach((item) => {
      const key = String(item[xFieldName]);
      if (!groupedMap.has(key)) {
        groupedMap.set(key, []);
        categoryOrder.push(key);
      }
      const yVal = Number(item[yFieldName]) || 0;
      const sizeVal =
        Number(item[sizeField]) || Number(item[yFieldName]) || pointSize * 10;
      groupedMap.get(key)!.push({ y: yVal, size: sizeVal });
    });

    let seriesCount = 0;
    groupedMap.forEach((values) => {
      seriesCount = Math.max(seriesCount, values.length);
    });

    seriesNames = Array.from(
      { length: seriesCount },
      (_, i) => `Series ${i + 1}`,
    );

    const allSizes: number[] = [];
    data.forEach((item) => {
      allSizes.push(
        Number(item[sizeField]) || Number(item[yFieldName]) || pointSize * 10,
      );
    });
    const minSize = Math.min(...allSizes);
    const maxSize = Math.max(...allSizes);

    seriesData = seriesNames.map((_, seriesIdx) =>
      categoryOrder
        .filter((key) => {
          const values = groupedMap.get(key)!;
          return seriesIdx < values.length;
        })
        .map((key) => {
          const entry = groupedMap.get(key)![seriesIdx];
          return {
            [xFieldName]: key,
            [yFieldName]: entry.y,
            z: normalizeBubbleSize(entry.size, minSize, maxSize),
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
          {map(seriesNames, (name, idx) => {
            const color = colorsArray[idx % colorsArray.length];
            return (
              <Scatter
                key={name}
                data={seriesData[idx]}
                fill={color}
                fillOpacity={0.6}
                name={name}
                animationDuration={600}
                animationEasing="ease-out"
                shape={(props: any) => {
                  const { cx, cy, payload } = props;
                  const size = (payload.z as number) || 50;
                  return (
                    <circle
                      cx={cx}
                      cy={cy}
                      r={size / 2}
                      fill={color}
                      fillOpacity={0.55}
                      stroke={color}
                      strokeWidth={1.5}
                      strokeOpacity={0.8}
                    />
                  );
                }}
              />
            );
          })}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};
