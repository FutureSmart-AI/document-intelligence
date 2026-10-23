import { map } from "lodash-es";
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

import { ChartTooltip } from "../components/ChartTooltip";
import type { DataRecord } from "../types";

interface PieChartProps {
  data: DataRecord[];
  xFieldName: string;
  yFieldName: string;
  colorsArray: string[];
  showLegend: boolean;
}

export const SimplePieChart = ({
  data,
  xFieldName,
  yFieldName,
  colorsArray,
  showLegend,
}: PieChartProps) => {
  // Aggregate duplicate categories by summing their values
  const aggregatedMap = new Map<string, number>();
  const categoryOrder: string[] = [];

  data.forEach((item) => {
    const name = String(item[xFieldName]);
    const value = Number(item[yFieldName] || 0);
    if (!aggregatedMap.has(name)) {
      aggregatedMap.set(name, 0);
      categoryOrder.push(name);
    }
    aggregatedMap.set(name, aggregatedMap.get(name)! + value);
  });

  const pieData = map(categoryOrder, (name) => ({
    name,
    value: aggregatedMap.get(name) ?? 0,
  }));

  const dataCount = pieData.length;
  const outerRadius = Math.min(130, Math.max(90, 110 - dataCount * 2));
  const innerRadius = outerRadius * 0.55;
  const total = pieData.reduce((sum, d) => sum + (d.value || 0), 0);

  return (
    <div style={{ width: "100%", minWidth: 0 }}>
      <ResponsiveContainer width="100%" height={300} minHeight={200}>
        <PieChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
          <Pie
            data={pieData}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            outerRadius={outerRadius}
            innerRadius={innerRadius}
            paddingAngle={2}
            cornerRadius={4}
            label={({ name, percent }: { name?: string; percent?: number }) => {
              if (!name || percent === undefined) return "";
              const displayName =
                name.length > 15 ? `${name.substring(0, 15)}...` : name;
              return `${displayName}: ${(percent * 100).toFixed(1)}%`;
            }}
            labelLine={{
              strokeWidth: 1.5,
              stroke: "#cbd5e1",
            }}
            animationDuration={600}
            animationEasing="ease-out"
          >
            {map(pieData, (entry, index) => (
              <Cell
                key={entry.name || `cell-${index}`}
                fill={colorsArray[index % colorsArray.length]}
                stroke="white"
                strokeWidth={2}
              />
            ))}
          </Pie>
          <Tooltip content={<ChartTooltip total={total} />} />
          {showLegend && (
            <Legend
              verticalAlign="bottom"
              height={36}
              iconType="circle"
              iconSize={8}
              wrapperStyle={{ paddingTop: 16, fontSize: 12 }}
              formatter={(value: string) =>
                value.length > 25 ? `${value.substring(0, 25)}...` : value
              }
            />
          )}
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
};
