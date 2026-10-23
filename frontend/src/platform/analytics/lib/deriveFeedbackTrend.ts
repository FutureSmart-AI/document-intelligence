import type { QueryRow } from "../types";
import { normalizeFeedback } from "./normalizeFeedback";

export interface FeedbackTrendPoint {
  bucket: string; // YYYY-MM-DD
  positive: number;
  negative: number;
}

/**
 * Bucket rated queries by day from a query-row sample. Rows without feedback
 * are skipped; days with no rated rows don't appear. Client-side
 * approximation — the backend has no daily feedback series.
 */
export function deriveFeedbackTrend(rows: QueryRow[]): FeedbackTrendPoint[] {
  const byDay = new Map<string, { positive: number; negative: number }>();

  for (const row of rows) {
    const rating = normalizeFeedback(row.feedback);
    if (!rating) continue;
    const day = row.timestamp?.slice(0, 10);
    if (!day) continue;
    const entry = byDay.get(day) ?? { positive: 0, negative: 0 };
    entry[rating] += 1;
    byDay.set(day, entry);
  }

  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([bucket, counts]) => ({ bucket, ...counts }));
}
