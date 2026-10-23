// src/components/session/session-countdown.ts

/** Show the countdown warning when the session has ≤ this long left. */
export const SESSION_WARNING_THRESHOLD_MS = 120_000;

/**
 * Attempt one silent renew (POST /refresh, then refetch /user/me) when the
 * session drops to ≤ this. Must be greater than SESSION_WARNING_THRESHOLD_MS so
 * a successful renew lifts the estimate back above the warning window before the
 * modal would appear.
 */
export const SESSION_SILENT_REFRESH_THRESHOLD_MS = 300_000;

export type CountdownAction = "idle" | "silentRefresh" | "warn" | "expire";

export interface CountdownThresholds {
  warnMs: number;
  silentMs: number;
}

/**
 * Parse the profile's `expires_at` into an absolute epoch-ms timestamp.
 *
 * The auth service returns a timezone-less ISO string (e.g. "2026-07-05T20:28:28")
 * that is actually UTC. `new Date(...)` would parse that as *local* time, so we
 * append "Z" when no timezone designator is present. Returns null for
 * missing/unparseable input.
 */
export function parseExpiresAt(
  expiresAt: string | null | undefined,
): number | null {
  if (!expiresAt) return null;
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(expiresAt);
  const normalized = hasTimezone ? expiresAt : `${expiresAt}Z`;
  const ms = new Date(normalized).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/**
 * Derive an absolute expiry (epoch ms) from a POST /refresh response body.
 *
 * The auth service returns `{ access_token, id_token, expires_in, token_type }` —
 * `expires_in` is a TTL in *seconds*, not an absolute timestamp, so the client
 * has to compute `now + expires_in`. An explicit `expires_at` is also accepted in
 * case the backend adds one later. Returns null when neither is usable.
 *
 * This matters because the refreshed expiry is not always observable through
 * GET /user/me: the two endpoints can derive the access-cookie name from
 * different sources (tenant frontend_url vs request Origin), so /user/me may
 * keep reporting the *old* expiry after a successful refresh. Trusting the
 * /refresh body keeps the countdown correct regardless.
 */
export function expiresAtFromRefresh(
  data: unknown,
  now: number = Date.now(),
): number | null {
  if (!data || typeof data !== "object") return null;
  const body = data as { expires_in?: unknown; expires_at?: unknown };

  const seconds =
    typeof body.expires_in === "string"
      ? Number(body.expires_in)
      : body.expires_in;
  if (typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0) {
    return now + seconds * 1000;
  }

  return typeof body.expires_at === "string"
    ? parseExpiresAt(body.expires_at)
    : null;
}

/** Format remaining milliseconds as "M:SS", rounding seconds up, clamping at 0. */
export function formatCountdown(ms: number): string {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/**
 * Decide what the controller should do this tick.
 * - expire: time is up → run session-expiry (redirect).
 * - warn: inside the warning window → show the countdown modal.
 * - silentRefresh: inside the silent window and not yet attempted → renew quietly.
 * - idle: nothing to do.
 */
export function deriveCountdownAction(
  msRemaining: number,
  silentRefreshDone: boolean,
  { warnMs, silentMs }: CountdownThresholds,
): CountdownAction {
  if (msRemaining <= 0) return "expire";
  if (msRemaining <= warnMs) return "warn";
  if (msRemaining <= silentMs && !silentRefreshDone) return "silentRefresh";
  return "idle";
}
