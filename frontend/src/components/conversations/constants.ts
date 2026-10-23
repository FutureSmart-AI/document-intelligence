/**
 * The two lists a collection's conversations are read as.
 *
 * Conversations are for the signed-in account's own threads. Users and Widget
 * activity belongs on Share → Activity, so the monitoring implementation stays
 * available but is intentionally not offered in this product's chat list.
 */
export type ConversationTab = "mine" | "users";

export const CONVERSATION_TABS = [
  { key: "mine", label: "My Conversations" },
] as const satisfies ReadonlyArray<{ key: ConversationTab; label: string }>;

/**
 * Users is a monitoring view, so it never sends, deletes or leaves feedback —
 * the same rule the agent UI applies to the tabs it shows an owner.
 */
export const isReadOnlyTab = (tab: ConversationTab): boolean => tab === "users";

/** Rows per page in an answer's result table, as in the design artifact. */
export const RESULT_PAGE_SIZE = 5;

/** Run-trace dot, by stage state. */
export const TRACE_DOT = {
  active: "bg-accent-soft text-accent",
  done: "bg-success-bg text-success-text",
  failed: "bg-danger-bg text-danger-text",
} as const;

/**
 * The three dots trailing a stage that is still running.
 *
 * A run can sit on "Thinking" for half a minute before the first chunk lands,
 * and a tick with a static label reads as a finished step that has stalled.
 * The wave is the one thing on screen that keeps saying the run is alive; the
 * classes are in `theme.css` so the delay stagger lives with the keyframe.
 */
export const THINKING_DOTS = [
  "di-thinking-dot",
  "di-thinking-dot di-thinking-dot-2",
  "di-thinking-dot di-thinking-dot-3",
] as const;

/**
 * Run-trace label, by stage state.
 *
 * A running stage takes its colour from the shimmer's clipped gradient rather
 * than a text utility — see `di-shimmer-text` in `styles/theme.css`. It also sets
 * its own size rather than taking the row's: it is the one line the reader is
 * waiting on, so it sits a step above the settled rows around it.
 */
export const TRACE_LABEL = {
  active: "text-[14px] font-semibold di-shimmer-text",
  done: "font-medium text-ink-2",
  failed: "font-semibold text-danger-text",
} as const;

/**
 * What the service's status strings mean for a document's dot.
 *
 * Sets rather than a list of `===` checks: the service reports several words
 * for each of the two settled states, and a document whose status is missing is
 * still being worked on rather than broken.
 */
/** Statuses the service reports as "this document is answerable". */
export const READY = new Set([
  "completed",
  "complete",
  "success",
  "processed",
  "done",
]);
/**
 * Statuses the service reports as settled and unreadable. `invalid` is a file
 * the batch route took but could not read — final, like `failed`, and never
 * "still being read".
 */
export const FAILED = new Set(["failed", "error", "invalid"]);

export type Health = "ready" | "working" | "failed";

export function health(status: string | undefined): Health {
  const value = (status ?? "").toLowerCase();
  if (READY.has(value)) return "ready";
  if (FAILED.has(value)) return "failed";
  return "working";
}

export const DOT: Record<Health, string> = {
  ready: "bg-success-dot",
  working: "bg-warn-dot animate-pulse",
  failed: "bg-danger-dot",
};

export const KIND: Record<string, string> = {
  file: "File",
  url: "Link",
  plain_text: "Text",
  clickup: "ClickUp page",
};

/**
 * What a result cell looks like, so a table of answers can right-align the
 * numbers without right-aligning an identifier that happens to be digits.
 */
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-/i;
export const DATEISH = /^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2}(:\d{2})?)?/;
export const CODE = /^[A-Z0-9]+(?:[-_][A-Z0-9]+)+$/;

/** Full-logs step dot, by what the step did. */
export const LOG_DOT = {
  done: "bg-success-bg text-success-text",
  failed: "bg-danger-bg text-danger-text",
  skipped: "bg-raised text-ink-3",
  flagged: "bg-warn-bg text-warn-text",
} as const;

/** Full-logs step label, by what the step did. */
export const LOG_LABEL = {
  done: "font-medium text-ink-2",
  failed: "font-semibold text-danger-text",
  skipped: "font-medium text-ink-3",
  flagged: "font-medium text-ink-2",
} as const;
