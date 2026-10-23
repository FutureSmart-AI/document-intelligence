import type { SchedulePreset } from "./types";

// API path templates — substituted via buildApiUrl({knowledge_id}).
// Behind the gateway every endpoint is /api/knowledge/v1/{knowledge_id}/clickup/...
export const CLICKUP_API = {
  CONNECT: "/api/knowledge/v1/{knowledge_id}/clickup/connect",
  SPACES: "/api/knowledge/v1/{knowledge_id}/clickup/spaces",
  FOLDERS: "/api/knowledge/v1/{knowledge_id}/clickup/folders",
  CONFIG: "/api/knowledge/v1/{knowledge_id}/clickup/config",
  SYNC: "/api/knowledge/v1/{knowledge_id}/clickup/sync",
  RUNS: "/api/knowledge/v1/{knowledge_id}/clickup/runs",
  RUN: "/api/knowledge/v1/{knowledge_id}/clickup/runs/{run_id}",
  RESYNC_DOC: "/api/knowledge/v1/{knowledge_id}/clickup/docs/{doc_id}/resync",
} as const;

export const MONTHLY_CRON = "0 0 1 * *";

// Schedule presets shown in Step 3. `minutes` is null for cron-/manual-only options.
export interface ScheduleOption {
  preset: SchedulePreset;
  label: string;
  description: string;
  minutes: number | null;
}

export const SCHEDULE_OPTIONS: ScheduleOption[] = [
  {
    preset: "1h",
    label: "Every hour",
    description: "Near real-time — heavy activity workspaces",
    minutes: 60,
  },
  {
    preset: "6h",
    label: "Every 6 hours",
    description: "Recommended default for most teams",
    minutes: 360,
  },
  {
    preset: "12h",
    label: "Every 12 hours",
    description: "Twice daily — moderate update frequency",
    minutes: 720,
  },
  {
    preset: "daily",
    label: "Daily",
    description: "Low-frequency doc updates",
    minutes: 1440,
  },
  {
    preset: "weekly",
    label: "Weekly",
    description: "Stable, rarely-changed docs",
    minutes: 10080,
  },
  {
    preset: "monthly",
    label: "Monthly",
    description: "Reference / archive content",
    minutes: null,
  },
  {
    preset: "custom",
    label: "Custom interval",
    description: "Set your own period",
    minutes: null,
  },
  {
    preset: "manual",
    label: "Manual only",
    description: "Sync on-demand from the dashboard",
    minutes: null,
  },
];

// Units for the custom-interval input.
export const CUSTOM_INTERVAL_UNITS: { label: string; minutes: number }[] = [
  { label: "hours", minutes: 60 },
  { label: "days", minutes: 1440 },
  { label: "weeks", minutes: 10080 },
];

// React Query key factory — scoped per knowledge base.
export const clickupKeys = {
  all: (kid: string | null) => ["clickup", kid] as const,
  config: (kid: string | null) => ["clickup", kid, "config"] as const,
  spaces: (kid: string | null) => ["clickup", kid, "spaces"] as const,
  folders: (kid: string | null, spaceId: string) =>
    ["clickup", kid, "folders", spaceId] as const,
  runs: (kid: string | null) => ["clickup", kid, "runs"] as const,
  run: (kid: string | null, runId: string) =>
    ["clickup", kid, "run", runId] as const,
};
