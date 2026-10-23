export type TraceState = "active" | "done" | "failed";

export interface TraceRow {
  key: string;
  label: string;
  /** Monospace detail shown after the label; omitted when there is none. */
  detail?: string;
  state: TraceState;
}

export interface TraceInput {
  /** Whether the run is still streaming. */
  loading: boolean;
  /** The answer text so far — empty until the first chunk lands. */
  answer: string;
  /** Documents the answer cited, when the agent shows its sources. */
  sourceCount: number;
  /** The chart the answer produced, when it produced one. */
  chartSuggestion?: string | null;
  /** Round trip the run reported, in ms. */
  latencyMs?: number | null;
  /** Sanitised failure text, when the run reported one. */
  error?: string | null;
}

function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;
}

/**
 * What the run did, as the answer arrives and once it lands.
 *
 * The same rule as the Database Agent's trace: every row is backed by something
 * the stream actually reported. The knowledge service emits no per-stage
 * progress events, so there are no timed placeholder stages — two live rows the
 * stream does evidence, then one completed row per fact the finished run
 * produced. What those facts are differs from a SQL run: documents read rather
 * than tables and rows.
 */
export function deriveTrace(input: TraceInput): TraceRow[] {
  const { loading, answer, sourceCount, chartSuggestion, latencyMs, error } =
    input;

  if (loading) {
    const writing = answer.trim().length > 0;
    return [
      {
        key: "reading",
        label: "Reading your documents",
        state: writing ? "done" : "active",
      },
      ...(writing
        ? [
            {
              key: "writing",
              label: "Writing the answer",
              state: "active" as const,
            },
          ]
        : []),
    ];
  }

  if (error) {
    return [
      { key: "reading", label: "Reading your documents", state: "done" },
      { key: "failed", label: "Run failed", detail: error, state: "failed" },
    ];
  }

  const rows: TraceRow[] = [];
  if (sourceCount > 0) {
    rows.push({
      key: "sources",
      label:
        sourceCount === 1 ? "Read 1 document" : `Read ${sourceCount} documents`,
      state: "done",
    });
  }
  if (chartSuggestion) {
    rows.push({
      key: "chart",
      label: "Built a chart",
      detail: chartSuggestion,
      state: "done",
    });
  }
  if (typeof latencyMs === "number") {
    rows.push({
      key: "latency",
      label: "Answered in",
      detail: formatMs(latencyMs),
      state: "done",
    });
  }
  return rows;
}
