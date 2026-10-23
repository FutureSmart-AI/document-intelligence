"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CHART_INITIAL_DIMENSION } from "../../constants";

export default function SuccessRateTrendChart({
  data,
}: {
  data: { bucket: string; success_rate?: number }[];
}) {
  const rows = data.map((d) => ({
    bucket: d.bucket.slice(0, 10),
    success_rate: d.success_rate ?? 0,
  }));
  return (
    <ResponsiveContainer
      width="100%"
      height="90%"
      initialDimension={CHART_INITIAL_DIMENSION}
    >
      <AreaChart
        data={rows}
        margin={{ top: 10, right: 10, left: 0, bottom: 10 }}
      >
        <defs>
          <linearGradient id="colorSuccess" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#22c55e" stopOpacity={0.15} />
            <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#0000000D" />
        <XAxis
          dataKey="bucket"
          stroke="#666"
          fontSize={12}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          stroke="#666"
          fontSize={12}
          tickLine={false}
          axisLine={false}
          domain={[0, 100]}
          unit="%"
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "white",
            border: "1px solid #e5e7eb",
            borderRadius: "6px",
            padding: "8px 12px",
          }}
        />
        <Area
          type="monotone"
          dataKey="success_rate"
          stroke="#22c55e"
          strokeWidth={3}
          fill="url(#colorSuccess)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
