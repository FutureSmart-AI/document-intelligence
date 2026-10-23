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

export default function CostTrendChart({
  data,
}: {
  data: { bucket: string; cost?: number; total_queries?: number }[];
}) {
  // Total cost per bucket + derived avg cost / query. Missing values pass through
  // as undefined (not 0) so recharts gaps the line instead of dropping to zero.
  const rows = data.map((d) => ({
    bucket: d.bucket.slice(0, 10),
    cost: d.cost,
    avg:
      d.cost != null && d.total_queries ? d.cost / d.total_queries : undefined,
  }));

  return (
    <ResponsiveContainer
      width="100%"
      height="90%"
      initialDimension={CHART_INITIAL_DIMENSION}
    >
      <LineChart
        data={rows}
        margin={{ top: 10, right: 20, left: 12, bottom: 10 }}
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
          yAxisId="cost"
          stroke="#666"
          fontSize={11}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `$${Number(v).toFixed(2)}`}
          domain={["auto", "auto"]}
          label={{
            value: "Total Cost",
            angle: -90,
            position: "insideLeft",
            style: {
              fill: "#6366f1",
              fontSize: 11,
              fontWeight: 600,
              textAnchor: "middle",
            },
          }}
        />
        <YAxis
          yAxisId="avg"
          orientation="right"
          stroke="#666"
          fontSize={11}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `$${Number(v).toFixed(3)}`}
          domain={["auto", "auto"]}
          label={{
            value: "Avg / Query",
            angle: 90,
            position: "insideRight",
            style: {
              fill: "#f59e0b",
              fontSize: 11,
              fontWeight: 600,
              textAnchor: "middle",
            },
          }}
        />
        <Tooltip
          formatter={(value: number, name: string) => [
            name === "Avg/Query"
              ? `$${Number(value).toFixed(4)}`
              : `$${Number(value).toFixed(2)}`,
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
          yAxisId="cost"
          type="monotone"
          dataKey="cost"
          name="Total Cost"
          stroke="#6366f1"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
        <Line
          yAxisId="avg"
          type="monotone"
          dataKey="avg"
          name="Avg/Query"
          stroke="#f59e0b"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
