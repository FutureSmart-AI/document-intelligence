import { AxiosError } from "axios";

// Internal backend config knob mentioned in the raw 400 detail. It must never
// be surfaced to end users (product review feedback).
const INTERNAL_CONFIG_VAR = "REPORTS_MAX_QUERIES_PER_REPORT";

// Backend phrasing: "A report may contain at most 20 queries (got 21). ..."
const QUERY_LIMIT_RE = /at most (\d+) queries/i;

const extractDetail = (error: unknown): string | undefined => {
  const detail = (error as AxiosError<{ detail?: string }>)?.response?.data
    ?.detail;
  return typeof detail === "string" && detail.trim() ? detail : undefined;
};

/**
 * Turn a batch-report mutation error into a message safe to show the user.
 *
 * The backend caps queries per report and returns an HTTP 400 whose `detail`
 * exposes the internal config variable. We rewrite the query-limit case into a
 * friendly, actionable message and never leak the config variable name.
 * Other backend detail messages pass through; anything else uses `fallback`.
 */
export const friendlyReportError = (
  error: unknown,
  fallback: string,
): string => {
  const detail = extractDetail(error);
  if (!detail) return fallback;

  const match = detail.match(QUERY_LIMIT_RE);
  if (match) {
    return `Report exceeds the maximum query limit (${match[1]}). Reduce the number of queries or increase the configured limit.`;
  }

  // Never leak the internal config variable if we couldn't rewrite the message.
  if (detail.includes(INTERNAL_CONFIG_VAR)) return fallback;

  return detail;
};
