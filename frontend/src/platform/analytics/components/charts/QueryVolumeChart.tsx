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
import { formatBucketTick } from "../../lib/formatBucketTick";

export default function QueryVolumeChart({
  data,
}: {
  data: { day: string; queries: number }[];
}) {
  return (
    <ResponsiveContainer
      width="100%"
      height="90%"
      initialDimension={CHART_INITIAL_DIMENSION}
    >
      <AreaChart
        data={data}
        margin={{ top: 10, right: 10, left: 0, bottom: 10 }}
      >
        <defs>
          <linearGradient id="colorQueries" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#6366f1" stopOpacity={0.1} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#0000000D" />
        <XAxis
          dataKey="day"
          stroke="#666"
          fontSize={12}
          tickLine={false}
          axisLine={false}
          // At hourly granularity the bucket is a full ISO timestamp, which
          // would run off the axis; a plain date is left exactly as it is.
          tickFormatter={formatBucketTick}
        />
        <YAxis
          stroke="#666"
          fontSize={12}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
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
          dataKey="queries"
          stroke="#6366f1"
          strokeWidth={3}
          fill="url(#colorQueries)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
