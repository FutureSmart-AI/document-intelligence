import type { RunStatus } from "@/platform/types/batch-reports";

/** Tone of the pill a run's status wears, in this product's own palette. */
export const RUN_TONE: Record<
  RunStatus | "queued",
  "success" | "accent" | "neutral" | "warn" | "danger"
> = {
  completed: "success",
  running: "accent",
  pending: "neutral",
  queued: "neutral",
  cancelled: "warn",
  failed: "danger",
};

/**
 * What a run says happened to one question.
 *
 * Only the three states the service states outright: it wrote an answer, it
 * wrote an error, or it has written neither yet. `relevance_score` is *not*
 * banded into a verdict here — the service documents no threshold for it, so any
 * cut-off would be this screen inventing a quality judgement and printing it as
 * if the service had made it. The score itself is shown beside the answer, which
 * is the honest version of the same information.
 */
export const ANSWER_VERDICT = {
  answered: { label: "Answered", tone: "success" as const },
  failed: { label: "Failed", tone: "danger" as const },
  pending: { label: "Waiting", tone: "neutral" as const },
};

/** A new report starts with one independent group holding one question. */
export const EMPTY_REPORT = {
  name: "",
  instructions: "",
  analysis: "",
  groups: [{ type: "independent" as const, queries: [""] }],
};

/** Rows-per-page choices for the run history, which the API does not page. */
export const HISTORY_ROWS = [5, 10, 25];

/**
 * Every schedule a report can be on, as the filter offers them.
 *
 * The whole vocabulary, not the schedules that happen to be in the loaded page:
 * a filter built from the rows on screen can only ever narrow to what is
 * already visible, and it changes shape as reports are added — so "Monthly"
 * appears and disappears from the list depending on data, which reads as a bug.
 * The values are what `scheduleShortLabel` derives from a stored schedule.
 */
export const SCHEDULE_FILTERS = [
  "Manual",
  "Daily",
  "Weekly",
  "Monthly",
  "Cron",
] as const;

/**
 * Every state a report's last run can be in, plus the one that is not a run.
 *
 * `RunStatus` covers the five the service reports; "Never run" is this list's
 * own row state — a report with no runs at all — and belongs in the same
 * dropdown because it is the answer to the same question.
 */
export const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "completed", label: "Completed" },
  { value: "running", label: "Running" },
  { value: "pending", label: "Pending" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "never", label: "Never run" },
];
