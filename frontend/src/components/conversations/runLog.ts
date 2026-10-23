import { isPlainObject } from "lodash-es";

/**
 * What the run did in the background, read off the turn.
 *
 * The knowledge service emits no per-stage progress events — the stream is a
 * run of `chunk`s and then one final payload — so this is a log read after the
 * fact rather than a live timeline. Every line is something the service
 * actually reported; nothing here is inferred from how long the UI waited.
 *
 * Two of the fields it is built from only exist on a live turn. `usage` (the
 * per-service latency, tokens and cost) and `debug_info` ride the final stream
 * event and are never stored, so a thread read back later has the steps and
 * their outcomes but no timings. `partial` says which of the two this is, and
 * the panel says so rather than printing zeroes.
 */

export interface RunLogCall {
  service: string;
  latencyMs?: number;
  provider?: string;
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
  costUsd?: number;
}

export type RunLogState = "done" | "failed" | "skipped" | "flagged";

export interface RunLogStep {
  key: string;
  label: string;
  /** One line summarising the step. */
  detail?: string;
  state: RunLogState;
  /** The step's own log lines, shown when it is unfolded. */
  lines?: string[];
  /** What this step cost, when the run reported it per service. */
  call?: RunLogCall;
}

export interface RunLog {
  steps: RunLogStep[];
  /** The run as a whole, when `usage` was there to total up. */
  totals?: { latencyMs?: number; totalTokens?: number; costUsd?: number };
  /** The pipeline the service says it ran, e.g. `vanilla_rag`. */
  runType?: string;
  model?: string;
  /** A thread read back from storage: steps, but no timings or cost. */
  partial: boolean;
}

const asObject = (value: unknown): Record<string, unknown> | null =>
  isPlainObject(value) ? (value as Record<string, unknown>) : null;

const num = (value: unknown): number | undefined => {
  const parsed = typeof value === "string" ? Number(value) : value;
  return typeof parsed === "number" && Number.isFinite(parsed)
    ? parsed
    : undefined;
};

const str = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() ? value : undefined;

export const formatMs = (ms: number): string =>
  ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;

/** Cost to the cent is useless at these magnitudes — four places, or "<$0.0001". */
export const formatCost = (usd: number): string =>
  usd > 0 && usd < 0.0001 ? "<$0.0001" : `$${usd.toFixed(4)}`;

const callOf = (usage: Record<string, unknown> | null, service: string) => {
  const calls = usage?.service_calls;
  if (!Array.isArray(calls)) return undefined;
  const found = calls.find((entry) => asObject(entry)?.service === service) as
    Record<string, unknown> | undefined;
  if (!found) return undefined;
  return {
    service,
    latencyMs: num(found.latency_ms),
    provider: str(found.provider),
    model: str(found.model),
    inputTokens: num(found.input_tokens),
    outputTokens: num(found.output_tokens),
    costUsd: num(found.cost_usd),
  } satisfies RunLogCall;
};

/**
 * The retrieval step's own lines: one per document, best score first.
 *
 * `detailed_chunks` is keyed by document id and holds the hits for each, with
 * the score as a string and the method that found them. The name is looked up
 * beside it, because a line of bare uuids is a log nobody can act on.
 *
 * `relevant_sources.data[].title` is what actually carries the name — measured
 * live, `summaries[id].filename` was absent for all thirteen documents of a
 * run, and a PDF has no `url` to fall back on, so reading only those two left
 * every uploaded file logged as a truncated id.
 */
function retrievalLines(knowledge: Record<string, unknown>): {
  lines: string[];
  methods: string[];
  best?: number;
  worst?: number;
} {
  const chunks = asObject(knowledge.detailed_chunks);
  const summaries = asObject(knowledge.summaries) ?? {};
  if (!chunks) return { lines: [], methods: [] };

  const retrieved = asObject(knowledge.relevant_sources)?.data;
  const titles = new Map<string, string>();
  if (Array.isArray(retrieved)) {
    for (const source of retrieved) {
      const entry = asObject(source);
      const id = str(entry?.id);
      const title = str(entry?.title);
      if (id && title) titles.set(id, title);
    }
  }

  const methods = new Set<string>();
  const rows: Array<{ score?: number; line: string }> = [];

  for (const [documentId, hits] of Object.entries(chunks)) {
    if (!Array.isArray(hits) || !hits.length) continue;
    const scores = hits
      .map((hit) => num(asObject(hit)?.relevance_score))
      .filter((score): score is number => score !== undefined);
    const top = scores.length ? Math.max(...scores) : undefined;
    for (const hit of hits) {
      const method = str(asObject(hit)?.search_method);
      if (method) methods.add(method);
    }
    const summary = asObject(summaries[documentId]);
    const name =
      titles.get(documentId) ??
      str(summary?.filename) ??
      str(asObject(hits[0])?.url) ??
      `${documentId.slice(0, 8)}…`;
    const score = top !== undefined ? top.toFixed(3) : "—";
    const count = hits.length === 1 ? "1 chunk" : `${hits.length} chunks`;
    rows.push({ score: top, line: `${score}  ${name}  (${count})` });
  }

  rows.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  const scored = rows
    .map((row) => row.score)
    .filter((score): score is number => score !== undefined);

  return {
    lines: rows.map((row) => row.line),
    methods: [...methods],
    best: scored.length ? Math.max(...scored) : undefined,
    worst: scored.length ? Math.min(...scored) : undefined,
  };
}

/**
 * The run block this product stashes on `data` so the log survives the stream.
 *
 * `useChatStream` rebuilds the finished assistant message from a fixed set of
 * fields and drops everything else the final event carried, but it merges and
 * keeps `data` — so the transport puts the run's own report in there rather
 * than this product widening a hook two other products share.
 */
export interface DiRunBlock {
  query?: string;
  refined_query?: string;
  is_answered?: string | boolean;
  llm_provider?: string;
  model?: string;
  usage?: Record<string, unknown>;
  debug_info?: Record<string, unknown>;
  byok_warnings?: unknown[];
  follow_up?: unknown[];
  /** Set when the turn was rebuilt from storage rather than from the stream. */
  stored?: boolean;
}

/** The log for one assistant turn, or null when the turn reported nothing. */
export function readRunLog(message: unknown): RunLog | null {
  const data = asObject(asObject(message)?.data);
  const knowledge = asObject(data?.knowledge);
  const run = (asObject(data?.di_run) ?? {}) as DiRunBlock;
  if (!knowledge && !Object.keys(run).length) return null;

  const usage = asObject(run.usage);
  const debug = asObject(run.debug_info);
  const steps: RunLogStep[] = [];

  // 1 — the question, as the service decided to ask it.
  const asked = str(run.query);
  const refined = str(run.refined_query);
  if (asked || refined) {
    const rewritten = !!asked && !!refined && asked.trim() !== refined.trim();
    steps.push({
      key: "question",
      label: rewritten ? "Rewrote the question" : "Read the question",
      detail: refined ?? asked,
      state: "done",
      lines: rewritten
        ? [`asked:   ${asked}`, `used:    ${refined}`]
        : undefined,
    });
  }

  // 2 — retrieval.
  if (knowledge) {
    const tool = str(asObject(knowledge.relevant_sources)?.tool_name);
    const { lines, methods, best, worst } = retrievalLines(knowledge);
    const sources = asObject(knowledge.relevant_sources)?.data;
    const count = Array.isArray(sources) ? sources.length : lines.length;
    if (count || lines.length) {
      const bits = [
        count === 1 ? "1 document" : `${count} documents`,
        ...(methods.length ? [methods.join(", ")] : []),
        ...(best !== undefined && worst !== undefined
          ? [`scores ${worst.toFixed(3)}–${best.toFixed(3)}`]
          : []),
      ];
      steps.push({
        key: "retrieval",
        label: tool
          ? `Searched the collection (${tool})`
          : "Searched the collection",
        detail: bits.join(" · "),
        state: "done",
        lines,
      });
    }
  }

  // 3 — the answer itself.
  const answerCall = callOf(usage, "generate_answer");
  const answered = run.is_answered;
  const couldAnswer =
    answered === undefined || answered === true || answered === "yes";
  if (answerCall || answered !== undefined || run.model) {
    const model = answerCall?.model ?? str(run.model);
    const provider = answerCall?.provider ?? str(run.llm_provider);
    steps.push({
      key: "answer",
      // The step always wrote an answer, so it is never the red row. What
      // `is_answered: "no"` reports is narrower than it sounds — measured on a
      // live two-part question, the run answered the half it had evidence for
      // and flagged the half it did not — so the flag is shown as a flag and
      // spelled out underneath, rather than as "could not answer" over a
      // perfectly good answer.
      label: "Wrote the answer",
      detail: [provider, model].filter(Boolean).join(" / ") || undefined,
      state: couldAnswer ? "done" : "flagged",
      lines: couldAnswer
        ? undefined
        : [
            "The service marked this answer as not fully covered by the",
            "retrieved evidence (is_answered: no). Part of the question may",
            "be unanswered, or answered from outside the documents.",
          ],
      call: answerCall,
    });
  }

  // 4 — the chart node, which runs whether or not it ends up drawing anything.
  const chart = asObject(knowledge?.chart_output);
  if (chart) {
    const drew = chart.chart_generated === true;
    const declined =
      chart.type === "direct_response" ||
      chart.chart_generated === false ||
      chart.graph_generated === false;
    const why = str(chart.reasoning) ?? str(chart.message);
    steps.push({
      key: "chart",
      label: drew
        ? `Built a ${str(chart.chart_suggestion) ?? "chart"}`
        : declined
          ? "Decided against a chart"
          : "Ran the chart step",
      detail: drew ? undefined : why?.split("\n")[0],
      state: declined && !drew ? "skipped" : "done",
      lines: !drew && why ? why.split("\n").slice(0, 40) : undefined,
      call: callOf(usage, "chart"),
    });
  }

  // 5 — anything the run wanted to warn about.
  const warnings = Array.isArray(run.byok_warnings) ? run.byok_warnings : [];
  if (warnings.length) {
    steps.push({
      key: "warnings",
      label:
        warnings.length === 1 ? "1 warning" : `${warnings.length} warnings`,
      state: "failed",
      lines: warnings.map((warning) =>
        typeof warning === "string" ? warning : JSON.stringify(warning),
      ),
    });
  }

  if (!steps.length) return null;

  const calls = Array.isArray(usage?.service_calls) ? usage.service_calls : [];
  const totals = usage
    ? {
        latencyMs: calls.reduce(
          (sum: number, entry) => sum + (num(asObject(entry)?.latency_ms) ?? 0),
          0,
        ),
        totalTokens: num(usage.total_tokens),
        costUsd: calls.reduce(
          (sum: number, entry) => sum + (num(asObject(entry)?.cost_usd) ?? 0),
          0,
        ),
      }
    : undefined;

  return {
    steps,
    totals,
    runType: str(debug?.type),
    model: callOf(usage, "generate_answer")?.model ?? str(run.model),
    // No `usage` means the turn came back from storage, where it is not kept.
    partial: !usage,
  };
}
