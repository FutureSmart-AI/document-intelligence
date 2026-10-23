"use client";

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

import { CHART_INITIAL_DIMENSION } from "../../constants";
import type { DailyTokens } from "../../lib/deriveDailyTokens";

const fmtBucket = (v: string) => {
  const d = new Date(v + "T00:00:00");
  return `${d.toLocaleString("default", { month: "short" })} ${d.getDate()}`;
};

const fmtTokens = (v: number) => {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return `${v}`;
};

export default function TokenTrendChart({ data }: { data: DailyTokens[] }) {
  return (
    <ResponsiveContainer
      width="100%"
      height="90%"
      initialDimension={CHART_INITIAL_DIMENSION}
    >
      <LineChart
        data={data}
        margin={{ top: 10, right: 8, left: 0, bottom: 10 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#0000000D" />
        <XAxis
          dataKey="bucket"
          stroke="#666"
          fontSize={11}
          tickLine={false}
          axisLine={false}
          tickFormatter={fmtBucket}
        />
        <YAxis
          stroke="#666"
          fontSize={11}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
          tickFormatter={fmtTokens}
        />
        <Tooltip
          formatter={(value: number, name: string) => [
            value.toLocaleString(),
            name,
          ]}
          contentStyle={{
            backgroundColor: "white",
            border: "1px solid #e5e7eb",
            borderRadius: "6px",
            padding: "8px 12px",
          }}
        />
        <Legend verticalAlign="top" align="right" height={28} />
        <Line
          type="monotone"
          dataKey="input"
          name="Input"
          stroke="#f59e0b"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
        <Line
          type="monotone"
          dataKey="output"
          name="Output"
          stroke="#22c55e"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
