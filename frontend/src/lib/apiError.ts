/**
 * What went wrong, in words a reader can act on.
 *
 * This app's axios instance has **no** error-toast interceptor — it only handles
 * 401 and session expiry (`src/utils/axios.ts`) — so a `catch` that says nothing
 * is a failure the reader never sees. Everything that writes therefore reports
 * through here.
 *
 * Three shapes come back from this service, and only the first is the obvious
 * one:
 *
 * - `{ detail: "You can only list your own knowledge services" }` — a sentence.
 * - `{ detail: [{ loc, msg, type }] }` — FastAPI validation, 422. Verified live:
 *   inviting `not-an-email` answers exactly that. The `msg` reads "Value error,
 *   must be a valid email address", so the "Value error, " prefix is trimmed.
 * - `{ message: "…" }` — a few write routes phrase it this way.
 *
 * Anything else falls back to the caller's own sentence rather than to axios's
 * "Request failed with status code 422", which tells a reader nothing.
 */

interface ValidationItem {
  msg?: string;
  loc?: unknown[];
}

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? (value as Record<string, unknown>) : {};

/** The field a validation error is about, e.g. `email`, for the message. */
function fieldOf(item: ValidationItem): string | undefined {
  const loc = Array.isArray(item.loc) ? item.loc : [];
  const last = loc.filter((part) => typeof part === "string").pop();
  return typeof last === "string" && last !== "body" ? last : undefined;
}

export function apiMessage(error: unknown, fallback: string): string {
  const response = asRecord(asRecord(error).response);
  const data = asRecord(response.data);
  const detail = data.detail ?? data.message;

  if (typeof detail === "string" && detail.trim()) return detail;

  if (Array.isArray(detail) && detail.length) {
    const messages = detail
      .map((entry) => {
        const item = asRecord(entry) as ValidationItem;
        const message = (item.msg ?? "").replace(/^Value error,\s*/i, "");
        if (!message) return undefined;
        const field = fieldOf(item);
        return field ? `${field}: ${message}` : message;
      })
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }

  // A thrown `Error` from our own code (the invite helper builds one from the
  // service's per-address failures) carries the useful sentence already.
  if (
    error instanceof Error &&
    error.message &&
    !/^Request failed/.test(error.message)
  ) {
    return error.message;
  }

  return fallback;
}
