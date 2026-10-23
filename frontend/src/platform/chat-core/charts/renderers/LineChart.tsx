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
import type { ChartData, DataRecord, ResolvedAxes } from "../types";
import { computeXAxisLayout, formatValue } from "../utils";

interface LineChartProps {
  data: DataRecord[];
  xFieldName: string;
  yFieldName: string;
  pointColor: string;
  pointSize: number;
  showLegend: boolean;
  metadata: ChartData["metadata"];
  axes: ResolvedAxes;
  integerY?: boolean;
}

export const SimpleLineChart = ({
  data,
  xFieldName,
  yFieldName,
  pointColor,
  pointSize,
  showLegend,
  metadata,
  axes,
  integerY,
}: LineChartProps) => {
  const xLabels = map(data, (d) => d[xFieldName]);
  const xLayout = computeXAxisLayout(xLabels);
  const lineColor = (metadata as any)?.line?.color ?? pointColor;
  const lineSmooth = (metadata as any)?.line?.smooth !== false;
  const lineWidth = (metadata as any)?.line?.width ?? 2.5;

  return (
    <div style={{ width: "100%", minWidth: 0 }}>
      <ResponsiveContainer width="100%" height={300} minHeight={200}>
        <LineChart
          data={data}
          margin={{ ...CHART_MARGINS, bottom: 30, left: 10 }}
        >
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
            dataKey={yFieldName}
            tick={AXIS_TICK_STYLE}
            axisLine={false}
            tickLine={false}
            allowDecimals={!integerY}
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
          <Line
            type={lineSmooth ? "monotone" : "linear"}
            dataKey={yFieldName}
            stroke={lineColor}
            strokeWidth={lineWidth}
            dot={{
              r: pointSize / 2,
              fill: lineColor,
              strokeWidth: 2,
              stroke: "white",
            }}
            activeDot={{
              r: 6,
              strokeWidth: 2,
              stroke: "white",
              fill: lineColor,
            }}
            name={axes.y.label}
            animationDuration={600}
            animationEasing="ease-out"
          />
          {metadata?.trend_line?.show && (
            <Line
              type="linear"
              dataKey={yFieldName}
              stroke={metadata.trend_line?.color || lineColor}
              strokeDasharray="5 5"
              strokeWidth={1.5}
              strokeOpacity={0.5}
              dot={false}
              name="Trend"
              animationDuration={800}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};
