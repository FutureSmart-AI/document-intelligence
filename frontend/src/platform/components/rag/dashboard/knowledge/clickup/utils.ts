import { MONTHLY_CRON, SCHEDULE_OPTIONS } from "./constants";
import type { ConfigRequest, SchedulePreset } from "./types";

/** Minimal schedule shape shared by ConnectorConfig and ConfigRequest. */
type ScheduleShape = {
  interval_minutes?: number | null;
  schedule_cron?: string | null;
};

/**
 * Parse a ClickUp doc id out of a pasted value. Accepts either a raw doc id
 * (e.g. "abc12de-3456") or a doc URL (e.g.
 * "https://app.clickup.com/1234567890/v/dc/abc12de-3456"). Returns the trimmed
 * id, or null if nothing usable was found.
 */
export const parseDocId = (raw: string): string | null => {
  const value = raw.trim();
  if (!value) return null;

  // URL form: the doc id is the path segment after `/dc/`.
  const dcMatch = value.match(/\/dc\/([A-Za-z0-9-]+)/);
  if (dcMatch) return dcMatch[1];

  // Not a URL — if it looks like a token (no spaces, no slashes) treat as a raw id.
  if (!/[\s/]/.test(value)) return value;

  return null;
};

/**
 * Resolve the ClickUp doc id for a knowledge-base row, or null if the row did
 * not originate from ClickUp / can't be mapped. Used to gate the per-row resync
 * action: it returns null today (backend exposes no doc id yet) and lights up
 * automatically once the documents API returns `clickup_doc_id`/`doc_id`, or a
 * ClickUp doc URL on a `content_type: "clickup"` row.
 */
export const resolveClickupDocId = (item: {
  content_type?: string;
  source?: string | null;
  clickup_doc_id?: string | null;
  doc_id?: string | null;
  url?: string | null;
  source_url?: string | null;
}): string | null => {
  const explicit = item.clickup_doc_id ?? item.doc_id;
  if (explicit) return explicit;
  const isClickup =
    item.content_type === "clickup" || item.source === "clickup";
  if (!isClickup) return null;
  return parseDocId(item.url ?? "") ?? parseDocId(item.source_url ?? "");
};

/** Normalise a keyword chip: trim + uppercase, drop empties. */
export const normalizeKeyword = (raw: string): string | null => {
  const value = raw.trim().toUpperCase();
  return value || null;
};

/**
 * Map a UI schedule selection to the mutually-exclusive
 * `interval_minutes` / `schedule_cron` config fields.
 *
 * - interval presets → `interval_minutes` set, `schedule_cron` cleared
 * - monthly → `schedule_cron` set, `interval_minutes` cleared
 * - manual → both cleared (null)
 */
export const scheduleToConfig = (
  preset: SchedulePreset,
  customMinutes?: number,
): Pick<ConfigRequest, "interval_minutes" | "schedule_cron"> => {
  if (preset === "manual") {
    return { interval_minutes: null, schedule_cron: null };
  }
  if (preset === "monthly") {
    return { interval_minutes: null, schedule_cron: MONTHLY_CRON };
  }
  if (preset === "custom") {
    const minutes = Math.max(1, Math.floor(customMinutes || 0));
    return { interval_minutes: minutes, schedule_cron: null };
  }
  const option = SCHEDULE_OPTIONS.find((o) => o.preset === preset);
  return { interval_minutes: option?.minutes ?? null, schedule_cron: null };
};

/**
 * Derive the active schedule preset from a saved config, for re-hydrating the
 * picker / edit form. Falls back to "custom" for any interval that isn't a
 * named preset, and "manual" when neither interval nor cron is set.
 */
export const configToSchedule = (config: ScheduleShape): SchedulePreset => {
  if (config.schedule_cron) {
    return config.schedule_cron === MONTHLY_CRON ? "monthly" : "custom";
  }
  if (config.interval_minutes == null) return "manual";
  const option = SCHEDULE_OPTIONS.find(
    (o) => o.minutes === config.interval_minutes,
  );
  return option ? option.preset : "custom";
};

/** Human-readable schedule label for the review / connected-sources screens. */
export const scheduleLabel = (config: ScheduleShape): string => {
  const preset = configToSchedule(config);
  if (preset === "manual") return "Manual only";
  if (preset === "monthly") return "Monthly (1st, 00:00 UTC)";
  if (preset === "custom") {
    if (config.schedule_cron) return `Cron: ${config.schedule_cron}`;
    const mins = config.interval_minutes ?? 0;
    if (mins % 1440 === 0) return `Every ${mins / 1440} days`;
    if (mins % 60 === 0) return `Every ${mins / 60} hours`;
    return `Every ${mins} minutes`;
  }
  const option = SCHEDULE_OPTIONS.find((o) => o.preset === preset);
  return option?.label ?? "Manual only";
};

/** Format an ISO timestamp as a relative "Xh ago" string, or "Never". */
export const relativeTime = (iso: string | null): string => {
  if (!iso) return "Never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "Never";
  const diffMs = Date.now() - then;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
};

/** True while a run is still in a non-terminal state (used to drive polling). */
export const isRunActive = (status: string | undefined): boolean =>
  status === "queued" || status === "running";
