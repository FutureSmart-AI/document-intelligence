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

interface ScatterChartProps {
  data: DataRecord[];
  xFieldName: string;
  yFieldName: string;
  pointColor: string;
  showLegend: boolean;
  axes: ResolvedAxes;
}

export const SimpleScatterChart = ({
  data,
  xFieldName,
  yFieldName,
  pointColor,
  showLegend,
  axes,
}: ScatterChartProps) => {
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
            data={data}
            fill={pointColor}
            fillOpacity={0.7}
            name={`${axes.x.label} vs ${axes.y.label}`}
            animationDuration={600}
            animationEasing="ease-out"
          />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};
