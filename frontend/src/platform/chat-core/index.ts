// Types
export type {
  PrimitiveValue,
  DataRecord,
  ChartData,
  ChatChartProps,
  NormalizedData,
  ResolvedAxes,
  SourceChunk,
  Source,
  DebugInfo,
  UserRole,
  Message,
  FeedbackPayload,
  FeedbackResponse,
} from "./types";

// Utilities
export {
  normalizeMarkdownTables,
  decodeEscapeChars,
  safeParseJson,
  copyToClipboard,
  processSources,
  processChunks,
  extractSources,
  extractFollowUpQuestions,
} from "./utils";

// Charts
export { ChatChart } from "./charts";
export { DEFAULT_COLORS, CHART_MARGINS } from "./charts/constants";
export { resolveAxes, normalizeData } from "./charts/utils";

// Streaming
export { SSEParser, streamSSERequest } from "./streaming";
export type {
  SSEParserConfig,
  ParsedSSEEvent,
  SSEError,
  StreamSSEOptions,
} from "./streaming";

// API
export { submitFeedback } from "./api";

// Markdown
export { getMarkdownComponents } from "./markdown";
export type { MarkdownComponentOverrides } from "./markdown";
