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
  answered: number;
  unanswered: number;
  prevAnswered?: number;
};

const fmtBucket = (v: string) => {
  const d = new Date(v + "T00:00:00");
  return `${d.toLocaleString("default", { month: "short" })} ${d.getDate()}`;
};

export default function AnsweredVsUnansweredChart({
  data,
}: {
  data: DataPoint[];
}) {
  const hasPrev = data.some((d) => d.prevAnswered != null);

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
          tickFormatter={(v: number) => `${v}%`}
          domain={[0, 100]}
        />
        <Tooltip
          formatter={(value: number, name: string) => [
            `${(value as number).toFixed(1)}%`,
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
          dataKey="answered"
          name="Answered"
          stroke="#22c55e"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
        <Line
          type="monotone"
          dataKey="unanswered"
          name="Unanswered"
          stroke="#ef4444"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
        {hasPrev && (
          <Line
            type="monotone"
            dataKey="prevAnswered"
            name="Prev period"
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
