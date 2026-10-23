import {
  ScheduleFrequency,
  ScheduleInput,
} from "@/platform/types/batch-reports";

// The backend ScheduleInput only stores { frequency, cron, timezone } — there
// is no separate time/day field. To let users pick an actual time (and day of
// week / month), the friendly builder encodes the selection into a cron
// expression and submits it as frequency:"cron". These helpers build the cron,
// parse it back for editing, and render a human-readable summary.

export type ScheduleMode = "" | "daily" | "weekly" | "monthly" | "cron";

export type ScheduleFields = {
  mode: ScheduleMode;
  time: string; // "HH:MM"
  dow: string; // cron day-of-week, "0"(Sun)–"6"(Sat)
  dom: string; // cron day-of-month, "1"–"31"
  cron: string; // raw expression (custom cron mode)
  timezone: string;
};

export const DOW_OPTIONS = [
  { value: "1", label: "Monday" },
  { value: "2", label: "Tuesday" },
  { value: "3", label: "Wednesday" },
  { value: "4", label: "Thursday" },
  { value: "5", label: "Friday" },
  { value: "6", label: "Saturday" },
  { value: "0", label: "Sunday" },
];

// The form uses prefixed field names (schedule_mode, schedule_time, …); this
// maps the ScheduleFields shape onto those keys for defaultValues / reset.
export const toFormSchedule = (f: ScheduleFields) => ({
  schedule_mode: f.mode,
  schedule_time: f.time,
  schedule_dow: f.dow,
  schedule_dom: f.dom,
  schedule_cron: f.cron,
  timezone: f.timezone,
});

// The user's current IANA timezone (falls back to UTC).
export const detectedTimezone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};

// Full list of IANA timezones from the runtime (no hardcoded list); UTC first,
// then the rest. Falls back to a small set on older engines.
export const getTimezones = (): string[] => {
  const supported = (
    Intl as unknown as { supportedValuesOf?: (k: string) => string[] }
  ).supportedValuesOf;
  const zones =
    typeof supported === "function"
      ? supported("timeZone")
      : ["UTC", "America/New_York", "Europe/London", "Asia/Kolkata"];
  return ["UTC", ...zones.filter((z) => z !== "UTC")];
};

export const defaultScheduleFields = (): ScheduleFields => ({
  mode: "",
  time: "09:00",
  dow: "1",
  dom: "1",
  cron: "",
  timezone: detectedTimezone(),
});

const dowLabel = (v: string) =>
  DOW_OPTIONS.find((o) => o.value === v)?.label ?? v;

const hhmm = (h: string, m: string) =>
  `${h.padStart(2, "0")}:${m.padStart(2, "0")}`;

// Build a 5-field cron expression from the friendly fields.
export const buildCron = (f: ScheduleFields): string => {
  if (f.mode === "cron") return f.cron.trim();
  const [h, m] = (f.time || "09:00").split(":");
  const min = Number(m) || 0;
  const hr = Number(h) || 0;
  if (f.mode === "daily") return `${min} ${hr} * * *`;
  if (f.mode === "weekly") return `${min} ${hr} * * ${f.dow}`;
  if (f.mode === "monthly") return `${min} ${hr} ${f.dom} * *`;
  return "";
};

// Convert form fields into the API payload (or null when no schedule).
export const fieldsToSchedule = (f: ScheduleFields): ScheduleInput | null => {
  if (!f.mode) return null;
  return {
    frequency: "cron" as ScheduleFrequency,
    cron: buildCron(f),
    timezone: f.timezone || "UTC",
  };
};

// Reverse: hydrate the form from a stored schedule (parses simple crons back
// into the friendly mode; falls back to raw cron mode when unrecognised).
export const scheduleToFields = (
  schedule: ScheduleInput | null | undefined,
): ScheduleFields => {
  const base = defaultScheduleFields();
  if (!schedule) return base;
  base.timezone = schedule.timezone || "UTC";

  // Legacy / semantic schedules without a cron expression.
  if (schedule.frequency !== "cron") {
    base.mode = schedule.frequency;
    return base;
  }

  const parts = (schedule.cron || "").trim().split(/\s+/);
  if (parts.length === 5) {
    const [min, hr, dom, mon, dow] = parts;
    if (mon === "*" && /^\d+$/.test(min) && /^\d+$/.test(hr)) {
      if (dom === "*" && dow === "*")
        return { ...base, mode: "daily", time: hhmm(hr, min) };
      if (dom === "*" && /^\d+$/.test(dow))
        return { ...base, mode: "weekly", time: hhmm(hr, min), dow };
      if (/^\d+$/.test(dom) && dow === "*")
        return { ...base, mode: "monthly", time: hhmm(hr, min), dom };
    }
  }
  return { ...base, mode: "cron", cron: schedule.cron ?? "" };
};

// Human-readable summary for the list / detail screens.
export const describeSchedule = (
  schedule: ScheduleInput | null | undefined,
): string => {
  if (!schedule) return "Manual runs only";
  const f = scheduleToFields(schedule);
  const tz = f.timezone ? ` · ${f.timezone}` : "";
  switch (f.mode) {
    case "daily":
      return `Daily · ${f.time}${tz}`;
    case "weekly":
      return `Weekly on ${dowLabel(f.dow)} · ${f.time}${tz}`;
    case "monthly":
      return `Monthly on day ${f.dom} · ${f.time}${tz}`;
    case "cron":
      return `Cron: ${f.cron}${tz}`;
    default:
      return "Manual runs only";
  }
};

// Short label for the list table's schedule pill.
export const scheduleShortLabel = (
  schedule: ScheduleInput | null | undefined,
): string | null => {
  if (!schedule) return null;
  const f = scheduleToFields(schedule);
  if (!f.mode) return null;
  return f.mode === "cron"
    ? "Cron"
    : f.mode.charAt(0).toUpperCase() + f.mode.slice(1);
};
