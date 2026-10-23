/**
 * The one refusal this service phrases as its own object rather than as a
 * sentence in `detail`.
 *
 * When a write would take the account past a platform limit, the knowledge
 * service answers with the whole picture instead of a message:
 *
 * ```json
 * {
 *   "error": "quota_exceeded",
 *   "message": "You have reached one of your account's usage limits. …",
 *   "remaining_quota": { "agents": -1, "conversations": 100, … },
 *   "would_exceed": [{ "limit_name": "max_agents", "current": 10,
 *                      "limit": 10, "requested": 1 }]
 * }
 * ```
 *
 * `apiMessage` would find the `message` and stop there, which loses the two
 * things that make the refusal actionable: *which* allowance ran out, and the
 * fact that the Limits tab is where you can see it. So this is parsed on its
 * own and reported by `useApiError`, which turns it into a toast that links
 * there.
 *
 * The body is matched, not the status: the service has answered this shape on
 * more than one code, and a reader being told "Request failed with status code
 * 403" for a full account is the failure this exists to prevent.
 *
 * `remaining_quota` is deliberately not read. It goes negative on the row that
 * refused (`agents: -1`), which is headroom arithmetic rather than anything a
 * reader should be shown; `would_exceed` already names the row in the
 * service's own terms.
 */

/** One allowance the request would have gone past. */
export interface QuotaBreach {
  /** The service's own key, e.g. `max_agents`. */
  limitName: string;
  current?: number;
  limit?: number;
  requested?: number;
}

export interface QuotaError {
  /** The service's sentence, or ours when it sent none. */
  message: string;
  breaches: QuotaBreach[];
}

const FALLBACK_MESSAGE = "You have reached one of your account's usage limits.";

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? (value as Record<string, unknown>) : {};

const asNumber = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

/**
 * `max_llm_tokens_per_month` → "LLM tokens", `max_messages_per_day` →
 * "Messages today" — the same words the Limits tab uses for its rows, so the
 * toast and the tab it sends you to name the allowance identically.
 */
export function limitLabel(limitName: string): string {
  const stripped = limitName.replace(/^max_/, "");
  const daily = stripped.endsWith("_per_day");
  const base = stripped.replace(/_per_month$/, "").replace(/_per_day$/, "");
  const words = base
    .split("_")
    .map((word, index) =>
      word === "llm"
        ? "LLM"
        : index === 0
          ? word.charAt(0).toUpperCase() + word.slice(1)
          : word,
    )
    .join(" ");
  return daily ? `${words} today` : words;
}

/** The quota refusal behind an error, or `null` if it is any other failure. */
export function quotaErrorOf(error: unknown): QuotaError | null {
  const data = asRecord(asRecord(asRecord(error).response).data);
  // FastAPI raises this through `HTTPException(detail=…)` on some routes and
  // returns it as the body on others, so both nestings are read.
  const body = [data, asRecord(data.detail)].find(
    (candidate) => candidate.error === "quota_exceeded",
  );
  if (!body) return null;

  const listed = Array.isArray(body.would_exceed) ? body.would_exceed : [];
  const breaches = listed
    .map((entry) => {
      const item = asRecord(entry);
      const limitName =
        typeof item.limit_name === "string" ? item.limit_name : "";
      if (!limitName) return null;
      const breach: QuotaBreach = {
        limitName,
        current: asNumber(item.current),
        limit: asNumber(item.limit),
        requested: asNumber(item.requested),
      };
      return breach;
    })
    .filter((breach): breach is QuotaBreach => breach !== null);

  const message =
    typeof body.message === "string" && body.message.trim()
      ? body.message.trim()
      : FALLBACK_MESSAGE;

  return { message, breaches };
}

/**
 * "Agents: 10 of 10 used." — the row that refused, with its figures.
 *
 * Empty when the service named no row: inventing "you are over a limit" for a
 * refusal that did not say which one would be this screen's claim, not the
 * service's.
 */
export function breachSentence(quota: QuotaError): string {
  return quota.breaches
    .map((breach) => {
      const label = limitLabel(breach.limitName);
      return breach.current !== undefined && breach.limit !== undefined
        ? `${label}: ${breach.current} of ${breach.limit} used.`
        : `${label} is at its limit.`;
    })
    .join(" ");
}
