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

const fmtBucket = (v: string) => {
  const d = new Date(v + "T00:00:00");
  return `${d.toLocaleString("default", { month: "short" })} ${d.getDate()}`;
};

const fmtSec = (ms: number) => `${(ms / 1000).toFixed(1)}s`;

export default function LatencyTrendChart({
  data,
}: {
  data: {
    bucket: string;
    avg_ms?: number;
    p95_ms?: number;
    prev_avg_ms?: number;
  }[];
}) {
  // Pass missing metrics through as undefined (not 0) so recharts gaps the
  // line instead of plotting a misleading drop-to-zero.
  const rows = data.map((d) => ({
    bucket: d.bucket.slice(0, 10),
    avg_ms: d.avg_ms,
    p95_ms: d.p95_ms,
    prev_avg_ms: d.prev_avg_ms,
  }));
  const hasPrev = rows.some((r) => r.prev_avg_ms != null);

  return (
    <ResponsiveContainer
      width="100%"
      height="90%"
      initialDimension={CHART_INITIAL_DIMENSION}
    >
      <LineChart
        data={rows}
        margin={{ top: 10, right: 16, left: 0, bottom: 10 }}
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
          tickFormatter={fmtSec}
          domain={["auto", "auto"]}
        />
        <Tooltip
          formatter={(value: number, name: string) => [fmtSec(value), name]}
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
          dataKey="avg_ms"
          name="Avg"
          stroke="#6366f1"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
        <Line
          type="monotone"
          dataKey="p95_ms"
          name="P95"
          stroke="#f59e0b"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
        {hasPrev && (
          <Line
            type="monotone"
            dataKey="prev_avg_ms"
            name="Prev"
            stroke="#9ca3af"
            strokeWidth={1.5}
            strokeDasharray="5 3"
            dot={false}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
