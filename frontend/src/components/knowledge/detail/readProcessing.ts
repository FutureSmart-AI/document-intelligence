import { isPlainObject } from "lodash-es";

/**
 * What processing one document took, read off its `processing_telemetry`.
 *
 * The worker measures it — how the file was read, which model extracted the
 * fields (and whether a fallback had to), the embeddings, the total — and the
 * service prices it with its one price table on read. Every number here is
 * one the service reported; a document processed before it was recorded has
 * none, and the panel is simply not shown.
 */

export interface ProcessingStep {
  key: string;
  label: string;
  /** "openai / gpt-5.4-nano", or the parser for the read step. */
  detail?: string;
  /** The model that was asked for, when a fallback did the work. */
  fallbackFrom?: string;
  fallbackReason?: string;
  latencyMs?: number;
  inputTokens?: number;
  cachedInputTokens?: number;
  outputTokens?: number;
  calls?: number;
  failedCalls?: number;
  /** undefined: not a priced step (parse). null: a model with no price. */
  costUsd?: number | null;
}

export interface ProcessingTelemetry {
  totalMs?: number;
  steps: ProcessingStep[];
  costUsd?: number;
  costComplete: boolean;
  unpricedModels: string[];
  priceTable?: string;
  /** The model that extracted the fields, for the one-line summary. */
  extractionModel?: string;
  usedFallback: boolean;
}

const asObject = (value: unknown): Record<string, unknown> | null =>
  isPlainObject(value) ? (value as Record<string, unknown>) : null;

const num = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

const str = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() ? value : undefined;

const PARSERS: Record<string, string> = {
  local_text: "local text reader",
  llamaparse: "LlamaParse",
  transcription: "transcription",
  plain_text: "plain text",
};

const OPERATION_LABEL: Record<string, string> = {
  extraction: "Extracted fields",
  embeddings: "Embedded for search",
};

export function readProcessingTelemetry(
  value: unknown,
): ProcessingTelemetry | null {
  const raw = asObject(value);
  if (!raw) return null;
  const steps: ProcessingStep[] = [];

  const parse = asObject(raw.parse);
  if (parse) {
    const parser = str(parse.parser);
    const mode = str(parse.mode);
    const reason = str(parse.fallback_reason);
    steps.push({
      key: "parse",
      label: "Read the document",
      detail: [parser ? (PARSERS[parser] ?? parser) : undefined, mode]
        .filter(Boolean)
        .join(" · "),
      latencyMs: num(parse.ms),
      fallbackReason: reason,
    });
  }

  let extractionModel: string | undefined;
  let usedFallback = false;
  const operations = Array.isArray(raw.operations) ? raw.operations : [];
  operations.forEach((entry, index) => {
    const op = asObject(entry);
    const operation = str(op?.operation);
    if (!op || !operation) return;
    const model = str(op.model);
    const requested = str(op.requested_model);
    const fallback = op.fallback === true;
    if (operation === "extraction" && (num(op.calls) ?? 0) > 0) {
      extractionModel = model;
      usedFallback ||= fallback;
    }
    const cost = op.estimated_cost_usd;
    // A reasoning model's effort is part of what ran: gpt-5.6-luna at "low" and
    // at "none" are different extraction configurations billed as one model.
    const effort = str(op.reasoning_effort);
    const served = [str(op.provider), model].filter(Boolean).join(" / ");
    steps.push({
      key: `${operation}-${index}`,
      label: OPERATION_LABEL[operation] ?? operation,
      detail: effort ? `${served} · reasoning ${effort}` : served,
      fallbackFrom: fallback && requested !== model ? requested : undefined,
      fallbackReason: fallback ? str(op.fallback_reason) : undefined,
      latencyMs: num(op.latency_ms),
      inputTokens: num(op.input_tokens),
      cachedInputTokens: num(op.cached_input_tokens),
      outputTokens: num(op.output_tokens),
      calls: num(op.calls),
      failedCalls: num(op.failed_calls),
      costUsd: cost === null ? null : num(cost),
    });
  });

  if (!steps.length && num(raw.total_ms) === undefined) return null;

  return {
    totalMs: num(raw.total_ms),
    steps,
    costUsd: num(raw.estimated_cost_usd),
    costComplete: raw.cost_complete !== false,
    unpricedModels: Array.isArray(raw.unpriced_models)
      ? raw.unpriced_models.filter((m): m is string => typeof m === "string")
      : [],
    priceTable: str(raw.price_table),
    extractionModel,
    usedFallback,
  };
}
