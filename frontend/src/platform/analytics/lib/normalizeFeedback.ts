// Per-query feedback rating, canonicalized. The Agent Health API documents
// `feedback` as "positive" | "negative" | null, but the value originates from
// `chat_logs.feedback` where the chat UI writes a boolean, and older rows /
// other writers used 1 / -1 — so accept every shape seen in the wild.
export type FeedbackRating = "positive" | "negative" | null;

export function normalizeFeedback(fb: unknown): FeedbackRating {
  if (fb === "positive" || fb === true || fb === 1) return "positive";
  if (fb === "negative" || fb === false || fb === -1) return "negative";
  return null;
}

/** Compact table cell label: 👍 / 👎 / — */
export function feedbackEmoji(fb: unknown): string {
  const rating = normalizeFeedback(fb);
  if (rating === "positive") return "👍";
  if (rating === "negative") return "👎";
  return "—";
}
