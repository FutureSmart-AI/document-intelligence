import dayjs from "dayjs";

/**
 * An axis label for one usage bucket.
 *
 * The scorecard labels each bucket with a plain date at daily granularity and
 * with a full ISO timestamp at hourly — `2026-08-07T13:00:00Z`, which is wider
 * than the axis and repeats its date twenty-four times a day. Anything
 * carrying a time is therefore shortened to the hour; a plain `YYYY-MM-DD` is
 * returned untouched, so the daily chart reads exactly as it did before the
 * granularity control existed.
 */
export function formatBucketTick(value: unknown): string {
  if (typeof value !== "string") return String(value ?? "");
  if (!value.includes("T")) return value;
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format("MMM D, HH:mm") : value;
}
