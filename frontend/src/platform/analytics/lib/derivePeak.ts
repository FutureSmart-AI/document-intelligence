export type PeakView = "hour" | "day" | "month";
export interface PeakBucket {
  label: string;
  value: number;
}

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** Parse a YYYY-MM-DD string as local time (avoids UTC-midnight day-shift). */
function parseLocal(day: string): Date {
  const [y, mo, d] = day.split("-").map(Number);
  return new Date(y, mo - 1, d);
}

/**
 * Hour-of-day distribution derived from individual query timestamps (the daily
 * `usage.per_day` has no hourly granularity). Bucketed in local time, 0–23,
 * over whatever query sample is passed in — returns null when the sample is
 * empty so the caller can show an empty state instead of a flat zero chart.
 */
export function deriveHourOfDay(
  timestamps: (string | null | undefined)[],
): PeakBucket[] | null {
  const counts = new Array(24).fill(0) as number[];
  let seen = 0;
  for (const ts of timestamps) {
    if (!ts) continue;
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) continue;
    counts[d.getHours()] += 1;
    seen += 1;
  }
  if (seen === 0) return null;
  return counts.map((value, h) => ({
    label: `${((h + 11) % 12) + 1} ${h < 12 ? "am" : "pm"}`, // 12 am,1 am … 11 pm
    value,
  }));
}

export function derivePeak(
  perDay: { day: string; queries: number }[],
  view: PeakView,
): PeakBucket[] | null {
  if (view === "hour") return null; // hour derived separately from timestamps

  if (view === "day") {
    const counts = [0, 0, 0, 0, 0, 0, 0];
    for (const { day, queries } of perDay) {
      const d = parseLocal(day);
      const idx = (d.getDay() + 6) % 7; // Sun=0 -> 6, Mon=1 -> 0
      counts[idx] += queries;
    }
    return DOW.map((label, i) => ({ label, value: counts[i] }));
  }

  // month: only months present in the data, in calendar order
  const byMonth = new Map<number, number>();
  for (const { day, queries } of perDay) {
    const m = parseLocal(day).getMonth();
    byMonth.set(m, (byMonth.get(m) ?? 0) + queries);
  }
  return [...byMonth.keys()]
    .sort((a, b) => a - b)
    .map((m) => ({ label: MONTHS[m], value: byMonth.get(m)! }));
}
