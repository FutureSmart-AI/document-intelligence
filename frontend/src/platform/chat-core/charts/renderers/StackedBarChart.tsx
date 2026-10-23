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
import { AXIS_LABEL_STYLE, AXIS_TICK_STYLE } from "../constants";
import type { DataRecord, ResolvedAxes } from "../types";
import { computeXAxisLayout, formatValue } from "../utils";

interface StackedBarChartProps {
  data: DataRecord[];
  xFieldName: string;
  yFieldName: string;
  series: string[];
  firstItem: DataRecord;
  isHorizontal: boolean;
  showLegend: boolean;
  colorsObj: Record<string, string>;
  colorsArray: string[];
  axes: ResolvedAxes;
}

export const StackedBarChart = ({
  data,
  xFieldName,
  series,
  firstItem,
  isHorizontal,
  showLegend,
  colorsObj,
  colorsArray,
  axes,
}: StackedBarChartProps) => {
  // Determine numeric series fields
  const numericFields =
    series.length > 0
      ? series
      : Object.keys(firstItem).filter((k) => {
          if (k === xFieldName || k.startsWith("_")) return false;
          return typeof (firstItem as any)[k] === "number";
        });

  if (numericFields.length === 0) return null;

  const dataCount = data.length;

  // Calculate optimal height: each bar gets ~40px; cap at 350 for mobile
  const barHeight = 40;
  const minHeight = 250;
  const calculatedHeight = Math.min(
    350,
    Math.max(minHeight, dataCount * barHeight + 100),
  );

  // Calculate legend space
  const legendItemCount = numericFields.length;
  const itemsPerRow = isHorizontal ? 5 : 3;
  const legendRows = Math.ceil(legendItemCount / itemsPerRow);
  const legendHeight = legendRows * 28 + 25;
  const legendWidth = isHorizontal ? 0 : Math.min(legendItemCount * 75, 300);

  // Compute dynamic x-axis layout for vertical mode
  const xLabels = map(data, (d) => d[xFieldName]);
  const xLayout = computeXAxisLayout(xLabels);

  return (
    <ResponsiveContainer width="100%" height={calculatedHeight} minHeight={200}>
      <BarChart
        data={data}
        layout={isHorizontal ? "vertical" : "horizontal"}
        margin={{
          top: 30,
          right: isHorizontal ? 30 : showLegend ? legendWidth : 30,
          left: isHorizontal ? 10 : 30,
          bottom: isHorizontal ? (showLegend ? legendHeight : 160) : 50,
        }}
      >
        <CartesianGrid
          stroke="#f1f5f9"
          vertical={!isHorizontal}
          horizontal={isHorizontal}
        />
        {isHorizontal ? (
          <>
            <XAxis
              type="number"
              tick={AXIS_TICK_STYLE}
              axisLine={false}
              tickLine={false}
              tickMargin={8}
              tickFormatter={(v) => formatValue(v)}
              label={{
                value: axes.y.label,
                position: "insideBottom",
                offset: -10,
                style: AXIS_LABEL_STYLE,
              }}
            />
            <YAxis
              type="category"
              dataKey={xFieldName}
              tick={{ fontSize: 13, fill: "#475569", fontWeight: 500 }}
              width={130}
              axisLine={false}
              tickLine={false}
              tickMargin={10}
              label={{
                value: axes.x.label,
                angle: -90,
                position: "insideLeft",
                offset: 0,
                style: { ...AXIS_LABEL_STYLE, textAnchor: "middle" },
              }}
            />
          </>
        ) : (
          <>
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
              tickMargin={8}
              label={{
                value: axes.x.label,
                position: "insideBottom",
                offset: 0,
                style: AXIS_LABEL_STYLE,
              }}
            />
            <YAxis
              type="number"
              tick={AXIS_TICK_STYLE}
              axisLine={false}
              tickLine={false}
              tickMargin={8}
              tickFormatter={(v) => formatValue(v)}
              label={{
                value: axes.y.label,
                angle: -90,
                position: "insideLeft",
                style: { ...AXIS_LABEL_STYLE, textAnchor: "middle" },
              }}
            />
          </>
        )}
        <Tooltip
          content={<ChartTooltip />}
          cursor={{
            fill: "rgba(99, 102, 241, 0.04)",
          }}
        />
        {showLegend && (
          <Legend
            verticalAlign={isHorizontal ? "bottom" : "top"}
            align={isHorizontal ? "center" : "right"}
            iconSize={8}
            iconType="circle"
            wrapperStyle={{
              paddingTop: isHorizontal ? 20 : 10,
              paddingLeft: isHorizontal ? 0 : 20,
              paddingRight: isHorizontal ? 0 : 20,
              paddingBottom: isHorizontal ? 5 : 0,
              maxWidth: isHorizontal ? "100%" : "300px",
              fontSize: 12,
            }}
            formatter={(value: string) => {
              const truncated =
                value.length > 20 ? `${value.slice(0, 20)}...` : value;
              return (
                <span style={{ color: "#475569", fontWeight: 400 }}>
                  {truncated}
                </span>
              );
            }}
          />
        )}
        {map(numericFields, (field, idx) => {
          const fill =
            colorsObj[field] || colorsArray[idx % colorsArray.length];
          const isLast = idx === numericFields.length - 1;

          return (
            <Bar
              key={`bar-${field}`}
              dataKey={field}
              stackId="stack"
              fill={fill}
              radius={
                isLast
                  ? isHorizontal
                    ? [0, 6, 6, 0]
                    : [6, 6, 0, 0]
                  : [0, 0, 0, 0]
              }
              name={field}
              stroke="#ffffff"
              strokeWidth={1}
              animationDuration={600}
              animationEasing="ease-out"
            />
          );
        })}
      </BarChart>
    </ResponsiveContainer>
  );
};
