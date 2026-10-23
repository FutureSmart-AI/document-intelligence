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

interface BubbleChartProps {
  data: DataRecord[];
  xFieldName: string;
  yFieldName: string;
  firstItem: DataRecord;
  pointColor: string;
  pointSize: number;
  showLegend: boolean;
  colorsArray: string[];
  axes: ResolvedAxes;
}

export const SimpleBubbleChart = ({
  data,
  xFieldName,
  yFieldName,
  firstItem,
  pointColor,
  pointSize,
  showLegend,
  colorsArray,
  axes,
}: BubbleChartProps) => {
  // For bubble charts, find size field or use y-axis value
  const dataKeys = Object.keys(firstItem);
  const sizeField =
    find(dataKeys, (k) => lowerCase(k) === "size") ||
    find(dataKeys, (k) => lowerCase(k) === "z") ||
    yFieldName;

  const bubbleData = map(data, (item) => {
    const sizeValue =
      Number(item[sizeField]) || Number(item[yFieldName]) || pointSize * 10;
    return { ...item, z: sizeValue };
  });

  const sizes = map(bubbleData, (item) => item.z as number);
  const minSize = Math.min(...sizes);
  const maxSize = Math.max(...sizes);
  const sizeRange = maxSize - minSize || 1;

  const normalizedData = map(bubbleData, (item) => ({
    ...item,
    z:
      minSize === maxSize
        ? 50
        : 20 + (((item.z as number) - minSize) / sizeRange) * 80,
  }));

  const xLabels = map(data, (d) => d[xFieldName]);
  const xLayout = computeXAxisLayout(xLabels);

  return (
    <div style={{ width: "100%", minWidth: 0 }}>
      <ResponsiveContainer width="100%" height={300} minHeight={200}>
        <ScatterChart margin={{ top: 20, right: 28, left: 10, bottom: 30 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            dataKey={xFieldName}
            name={axes.x.label}
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
          <Scatter
            data={normalizedData}
            fill={pointColor}
            fillOpacity={0.6}
            name={`${axes.x.label} vs ${axes.y.label}`}
            animationDuration={600}
            animationEasing="ease-out"
            shape={(props) => {
              const { cx, cy, payload } = props;
              const size = (payload.z as number) || 50;
              const index = normalizedData.findIndex(
                (item) => item === payload,
              );
              const fillColor =
                colorsArray[index % colorsArray.length] || pointColor;
              return (
                <circle
                  cx={cx}
                  cy={cy}
                  r={size / 2}
                  fill={fillColor}
                  fillOpacity={0.55}
                  stroke={fillColor}
                  strokeWidth={1.5}
                  strokeOpacity={0.8}
                />
              );
            }}
          />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};
