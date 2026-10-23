import type { QueryRow } from "../types";

export interface DailyTokens {
  bucket: string; // YYYY-MM-DD
  input: number;
  output: number;
}

/**
 * Daily input/output token totals derived from individual query records.
 * The trends endpoint has no per-bucket token metric (only cost), so we roll
 * up the recent-query sample (`metrics.input_tokens` / `output_tokens` keyed by
 * `timestamp`) client-side — an approximation over the latest activity, the
 * same tactic used for the hour-of-day peak. Returns [] when the sample is
 * empty so the caller can show an empty state.
 */
export function deriveDailyTokens(
  queries: QueryRow[] | undefined,
): DailyTokens[] {
  if (!queries?.length) return [];
  const byDay = new Map<string, { input: number; output: number }>();
  for (const q of queries) {
    if (!q.timestamp) continue;
    const d = new Date(q.timestamp);
    if (Number.isNaN(d.getTime())) continue;
    // Local calendar day, matching the daily buckets shown elsewhere.
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(
      2,
      "0",
    )}-${String(d.getDate()).padStart(2, "0")}`;
    const acc = byDay.get(key) ?? { input: 0, output: 0 };
    acc.input += q.metrics?.input_tokens ?? 0;
    acc.output += q.metrics?.output_tokens ?? 0;
    byDay.set(key, acc);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([bucket, v]) => ({ bucket, input: v.input, output: v.output }));
}
