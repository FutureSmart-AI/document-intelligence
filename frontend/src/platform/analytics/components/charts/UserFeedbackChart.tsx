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

type DataPoint = {
  bucket: string;
  positive: number;
  negative: number;
};

const fmtBucket = (v: string) => {
  const d = new Date(v + "T00:00:00");
  return `${d.toLocaleString("default", { month: "short" })} ${d.getDate()}`;
};

export default function UserFeedbackChart({ data }: { data: DataPoint[] }) {
  return (
    <ResponsiveContainer
      width="100%"
      height="90%"
      initialDimension={CHART_INITIAL_DIMENSION}
    >
      <LineChart
        data={data}
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
        <Legend verticalAlign="top" align="right" height={28} />
        <Line
          type="monotone"
          dataKey="positive"
          name="Positive"
          stroke="#22c55e"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
        <Line
          type="monotone"
          dataKey="negative"
          name="Negative"
          stroke="#ef4444"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
