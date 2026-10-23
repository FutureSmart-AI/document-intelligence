/**
 * A single parsed SSE event from the chat stream.
 */
export interface ParsedSSEEvent {
  chunk?: string;
  response?: string;
  debug_info?: any;
  session_id?: string;
  follow_up?: string[];
  chat_id?: string;
  data?: any;
  /** The run's own id in the service's logs, when it reports one. */
  trace_id?: string;
  /** The service's own verdict: false means the run did not answer. */
  is_answered?: boolean;
  /**
   * A run that broke after the stream opened. The response is still 200 and the
   * stream still ends normally, so this is the only sign the answer failed.
   */
  error?: string;
}

/**
 * Error from the SSE stream.
 */
export interface SSEError {
  type: "timeout" | "quota" | "network" | "parse" | "forbidden" | "unknown";
  message: string;
  /** HTTP status code, when the error originated from a non-ok response. */
  status?: number;
  original?: unknown;
}

/**
 * Configuration for the SSE parser.
 */
export interface SSEParserConfig {
  /** Timeout in milliseconds. Default: 90000 (90s) */
  timeoutMs?: number;
  /** Throttle interval for onChunk calls in ms. Default: 50 */
  throttleMs?: number;
  /** Called for each parsed SSE event */
  onChunk: (event: ParsedSSEEvent) => void;
  /** Called when the stream completes */
  onComplete: () => void;
  /** Called on error */
  onError: (error: SSEError) => void;
}
