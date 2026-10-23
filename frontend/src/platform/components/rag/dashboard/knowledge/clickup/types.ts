// Types for the ClickUp knowledge-source connector.
// Mirrors the backend contract in `routers/clickup_sync.py` (see task spec).

/** A space or a folder returned by ClickUp listing endpoints. */
export interface ClickUpNode {
  id: string;
  name: string;
}

export type RunStatus = "queued" | "running" | "completed" | "failed";
export type RunTrigger = "manual" | "scheduled";

/**
 * Stored selection blob — populated incrementally by Connect + config saves.
 * All keys optional; only keys that have been set are returned by the API.
 */
export interface Selection {
  workspace_id?: string;
  space_ids?: string[];
  folder_ids?: string[];
  folder_names?: string[];
  exclude_folder_name?: string[];
  exclude_folder_id?: string[];
  exclude_name_keywords?: string[];
  space_docs_enabled?: boolean;
  space_doc_allowlist?: string[];
  deletion_enabled?: boolean;
}

/** Returned by GET/PUT /config. The token is never returned — only a presence flag. */
export interface ConnectorConfig {
  id: number;
  knowledge_id: string;
  source: "clickup";
  enabled: boolean;
  token: "configured" | null;
  selection: Selection;
  interval_minutes: number | null;
  schedule_cron: string | null;
  last_sync_at: string | null;
  next_sync_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** One row in GET /runs and GET /runs/{run_id}. */
export interface SyncRun {
  run_id: string;
  status: RunStatus;
  trigger: RunTrigger;
  indexed: number;
  skipped: number;
  errors: number;
  deleted: number;
  error_detail: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
}

export interface ConnectRequest {
  api_token: string;
  workspace_id: string;
}

export interface ConnectResponse {
  valid: true;
  workspace_id: string;
  spaces: ClickUpNode[];
}

/** Request body for PUT /config. Every field optional; only sent fields are written. */
export interface ConfigRequest {
  space_ids?: string[];
  folder_ids?: string[];
  folder_names?: string[];
  doc_ids?: string[];
  exclude_folder_name?: string[];
  exclude_folder_id?: string[];
  exclude_name_keywords?: string[];
  space_docs_enabled?: boolean;
  space_doc_allowlist?: string[];
  deletion_enabled?: boolean;
  force_reindex?: boolean;
  interval_minutes?: number | null;
  schedule_cron?: string | null;
  enabled?: boolean;
}

export interface SyncResponse {
  run_id: string;
  status: "queued";
  force_reindex: boolean;
}

export interface ResyncResponse {
  run_id: string;
  status: "queued";
  doc_id: string;
  message?: string;
}

export interface DisconnectResponse {
  disconnected: true;
  purge_docs: boolean;
  docs_purged: number;
  message?: string;
}

/** Preset keys used by the schedule picker UI. */
export type SchedulePreset =
  "1h" | "6h" | "12h" | "daily" | "weekly" | "monthly" | "custom" | "manual";
