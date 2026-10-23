import { get, map } from "lodash-es";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
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

interface BarChartProps {
  data: DataRecord[];
  xFieldName: string;
  yFieldName: string;
  colorsArray: string[];
  showLegend: boolean;
  axes: ResolvedAxes;
  integerY?: boolean;
}

export const SimpleBarChart = ({
  data,
  xFieldName,
  yFieldName,
  colorsArray,
  showLegend,
  axes,
  integerY,
}: BarChartProps) => {
  const dataWithColors = map(data, (item, index) => ({
    ...item,
    _color: colorsArray[index % colorsArray.length],
  }));

  const xLabels = map(data, (d) => d[xFieldName]);
  const xLayout = computeXAxisLayout(xLabels);
  const chartMargins = { ...CHART_MARGINS, bottom: 30, left: 10 };

  return (
    <div style={{ width: "100%", minWidth: 0 }}>
      <ResponsiveContainer width="100%" height={300} minHeight={200}>
        <BarChart data={dataWithColors} margin={chartMargins}>
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
          <Bar
            dataKey={yFieldName}
            radius={[6, 6, 0, 0]}
            name={axes.y.label}
            animationDuration={600}
            animationEasing="ease-out"
          >
            {map(dataWithColors, (entry, index) => (
              <Cell
                key={get(entry, xFieldName) || `cell-${index}`}
                fill={entry._color as string}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};
