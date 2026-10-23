import type { ChartData } from "./chart";
import type { DebugInfo } from "./source";

export type UserRole = "user" | "assistant";

/**
 * Unified message interface used by both the dashboard and widget.
 * Dashboard-specific fields (sources as string[], user_feedback) and
 * widget-specific fields (id, timestamp, chatIdPrev) are all optional.
 */
export interface Message {
  id?: string;
  chat_id?: string;
  role?: UserRole | string;
  message?: string;
  content?: string;
  sources?: any[];
  user_feedback?: string;
  debug_info?: DebugInfo | string | null;
  follow_up?: string[];
  /**
   * The run behind this answer, as the service reported it.
   *
   * `trace_id` is the only handle tying a turn on screen to its run in the
   * service's logs; `is_answered` is the run's own verdict on whether it
   * answered. Both are carried onto the message so the run log can show them.
   */
  trace_id?: string | null;
  is_answered?: boolean | null;
  /** The thread this turn belongs to, as the service reported it. */
  session_id?: string | null;
  timestamp?: Date;
  chatIdPrev?: string | null;
  /** True when this user message was dictated via the speech-to-text mic. */
  via_voice?: boolean;
  data?: {
    nl2sql?: {
      chart_data?: ChartData;
      chart_suggestion?: string;
      sql_query?: string;
      tables_used?: string[];
    };
    knowledge?: {
      sources?: any[];
      chunks?: any[];
      summaries?: Record<string, any>;
    };
    [key: string]: unknown;
  };
}
