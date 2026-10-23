// Batch Query Reports — types mirror the backend API reference
// (batch_query_reports/router_restful.py + pydantic_models_restful.py).
// All endpoints live under root_path /api, e.g. /api/dashboard/v1/reports.
// agent_id is a required query param on every endpoint.

// ============================================================================
// Enums (exact string values from §C of the API reference)
// ============================================================================

export type QueryGroupType = "independent" | "sequential";

export type ScheduleFrequency = "daily" | "weekly" | "monthly" | "cron";

export type RunStatus =
  "pending" | "running" | "completed" | "failed" | "cancelled";

export type RunTrigger = "manual" | "scheduled";

export type ExportFormat = "json" | "md" | "csv";

// Terminal statuses: completed, failed, cancelled.

// ============================================================================
// Request types
// ============================================================================

export type QueryGroupInput = {
  type: QueryGroupType;
  queries: string[];
};

export type ScheduleInput = {
  frequency: ScheduleFrequency;
  cron?: string | null; // required only when frequency === "cron"
  timezone?: string | null; // IANA tz, defaults to "UTC" server-side
};

export type CreateReportRequest = {
  name: string;
  query_groups: QueryGroupInput[];
  report_instructions?: string | null;
  analysis_prompt?: string | null;
  schedule?: ScheduleInput | null;
  run_immediately?: boolean; // default false; if true, also enqueues a run
};

// Partial update — send only fields to change.
export type UpdateReportRequest = {
  name?: string | null;
  report_instructions?: string | null;
  query_groups?: QueryGroupInput[] | null;
  analysis_prompt?: string | null;
  schedule?: ScheduleInput | null;
};

// ============================================================================
// Simple action / write responses
// ============================================================================

// A1 POST /reports (201) — run_id present only when run_immediately=true.
export type CreateReportResponse = {
  report_id: string;
  run_id?: string;
};

// A4 / A5 — update & soft-delete.
export type MessageResponse = {
  message: string;
};

// A6 run (202) and A11 cancel (200).
export type RunActionResponse = {
  run_id: string;
  status: RunStatus;
  message?: string;
};

// ============================================================================
// Per-agent report settings (query limit)
// ============================================================================

// GET /reports/settings — owner-only (editor/viewer → 403).
export type ReportSettings = {
  max_queries_per_report: number; // effective limit for this agent
  default_max_queries_per_report: number; // system default
};

// PUT /reports/settings body.
export type UpdateReportSettingsRequest = {
  max_queries_per_report: number; // integer 1–1000
};

// PUT /reports/settings response.
export type UpdateReportSettingsResponse = {
  message: string;
  max_queries_per_report: number;
  default_max_queries_per_report: number;
};

// ============================================================================
// Report read models (A2 list, A3 get)
// ============================================================================

export type ReportSummary = {
  report_id: string;
  agent_id: string;
  user_id: string;
  name: string;
  report_instructions: string | null;
  query_groups: QueryGroupInput[];
  analysis_prompt: string | null;
  schedule: ScheduleInput | null;
  created_at: string;
  updated_at: string;
};

export type ListReportsResponse = {
  reports: ReportSummary[];
};

// ============================================================================
// Run read models
// ============================================================================

export type RunProgress = {
  total_queries: number;
  completed_queries: number;
  failed_queries: number;
  percent_complete: number; // round((completed + failed) / total * 100)
};

// A7 list runs item.
export type RunListItem = {
  run_id: string;
  report_id: string;
  status: RunStatus;
  trigger: RunTrigger;
  progress: RunProgress;
  started_at: string | null; // null until started
  ended_at: string | null; // null until terminal
  created_at: string;
  total_time_ms: number | null;
};

export type ListRunsResponse = {
  runs: RunListItem[];
};

// A9 status (poll).
export type RunStatusResponse = {
  run_id: string;
  report_id: string;
  agent_id: string;
  status: RunStatus;
  progress: RunProgress;
  started_at: string | null;
  ended_at: string | null;
};

// A8 full run result -------------------------------------------------------

export type RunSummary = {
  total_queries: number;
  completed_queries: number;
  failed_queries: number;
  avg_response_time_ms: number;
  total_tokens: number;
  total_time_ms: number | null;
};

// Backend returns loosely-shaped source/document objects; keep permissive.
export type SourceUsed = {
  title?: string;
  url?: string;
  [key: string]: unknown;
};

export type RetrievedDocument = {
  doc_id?: string;
  chunk?: string;
  [key: string]: unknown;
};

export type RetrievedChunk = {
  chunk_id?: string;
  content?: string;
  text?: string;
  doc_id?: string;
  score?: number;
  [key: string]: unknown;
};

// SQL-agent execution details — same shape as chat's data.nl2sql. Populated for
// SQL agents, null for knowledge agents. Always present in the response.
export type ExecutionDetails = {
  sql_query?: string | null;
  tables_used?: string[] | null;
  db_result?: {
    columns?: string[];
    rows?: Record<string, unknown>[];
    [key: string]: unknown;
  } | null;
  export_url?: string | null;
  chart_suggestion?: string | null;
  chart_data?: Record<string, unknown> | null;
  [key: string]: unknown;
};

export type QueryResult = {
  question: string;
  query_id: string;
  group_id: string;
  // The following are absent until the Worker writes a result:
  session_id?: string;
  answer: string | null;
  answer_ref?: string | null; // non-null only if body stored in S3 by reference
  sources_used: SourceUsed[];
  retrieved_documents: RetrievedDocument[];
  retrieved_chunks: RetrievedChunk[];
  relevance_score: number | null;
  response_time_ms?: number;
  token_usage?: number;
  // SQL agents → populated; knowledge agents → null. Always present.
  execution_details?: ExecutionDetails | null;
  error: string | null; // non-null → this query failed
};

export type ConversationTurn = {
  question: string;
  answer: string;
};

export type RunGroupResult = {
  group_id: string;
  type: QueryGroupType;
  results: QueryResult[];
  // Present ONLY for sequential groups.
  conversation_flow?: ConversationTurn[];
};

export type RunResult = {
  run_id: string;
  report_id: string;
  name: string | null; // null if parent report row is gone
  agent_id: string;
  status: RunStatus;
  trigger: RunTrigger;
  executed_at: string; // started_at, or created_at if never started
  started_at: string | null;
  ended_at: string | null;
  failure_reason: string | null; // set only on whole-job failure
  summary: RunSummary;
  overall_summary: string | null; // analysis_prompt output; null if none set
  query_groups: RunGroupResult[];
};
