"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CHART_INITIAL_DIMENSION } from "../../constants";

export interface LatencyBucket {
  label: string;
  count: number;
}

// Fixed latency buckets matching the design. The backend doesn't yet expose a
// latency-distribution aggregate, so callers pass an empty array and the tab
// shows an empty state; the component is ready to render once data exists.
export const LATENCY_BUCKETS = [
  "<1s",
  "1-2s",
  "2-3s",
  "3-5s",
  "5-8s",
  "8-12s",
  "12-20s",
  "20-30s",
  ">30s",
] as const;

export default function ResponseTimeDistributionChart({
  data,
}: {
  data: LatencyBucket[];
}) {
  return (
    <ResponsiveContainer
      width="100%"
      height="90%"
      initialDimension={CHART_INITIAL_DIMENSION}
    >
      <BarChart
        data={data}
        margin={{ top: 10, right: 10, left: 0, bottom: 10 }}
      >
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="#0000000D"
          vertical={false}
        />
        <XAxis
          dataKey="label"
          stroke="#666"
          fontSize={11}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          stroke="#666"
          fontSize={11}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
        />
        <Tooltip
          cursor={{ fill: "#6366f10D" }}
          contentStyle={{
            backgroundColor: "white",
            border: "1px solid #e5e7eb",
            borderRadius: "6px",
            padding: "8px 12px",
          }}
        />
        <Bar
          dataKey="count"
          name="Queries"
          fill="#6366f1"
          radius={[4, 4, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
