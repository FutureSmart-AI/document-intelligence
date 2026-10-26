import { describe, expect, it } from "vitest";

import { readProcessingTelemetry } from "../readProcessing";

/** `processing_telemetry` as GET /v1/{kid}/documents/{id} returns it. */
const telemetry = {
  total_ms: 9100,
  parse: { parser: "local_text", mode: "text_based", ms: 420 },
  operations: [
    {
      operation: "extraction",
      provider: "openai",
      model: "gpt-5.4-nano",
      requested_model: "gemini-3.1-flash-lite",
      fallback: true,
      fallback_reason: "primary_failed: 402",
      calls: 1,
      input_tokens: 12000,
      cached_input_tokens: 2000,
      output_tokens: 900,
      latency_ms: 7300,
      estimated_cost_usd: 0.003565,
    },
    {
      operation: "embeddings",
      provider: "openai",
      model: "text-embedding-3-small",
      calls: 1,
      input_tokens: 5000,
      output_tokens: 0,
      latency_ms: 600,
      estimated_cost_usd: 0.0001,
    },
  ],
  estimated_cost_usd: 0.003665,
  cost_complete: true,
  unpriced_models: [],
  price_table: "openai-standard-2026-10-26",
};

describe("readProcessingTelemetry", () => {
  it("lists the read, extraction and embedding steps with their numbers", () => {
    const read = readProcessingTelemetry(telemetry)!;
    expect(read.steps.map((step) => step.label)).toEqual([
      "Read the document",
      "Extracted fields",
      "Embedded for search",
    ]);
    expect(read.steps[0]).toMatchObject({
      detail: "local text reader · text_based",
      latencyMs: 420,
    });
    expect(read.steps[1]).toMatchObject({
      detail: "openai / gpt-5.4-nano",
      fallbackFrom: "gemini-3.1-flash-lite",
      cachedInputTokens: 2000,
      costUsd: 0.003565,
    });
    expect(read).toMatchObject({
      totalMs: 9100,
      costUsd: 0.003665,
      costComplete: true,
      extractionModel: "gpt-5.4-nano",
      usedFallback: true,
      priceTable: "openai-standard-2026-10-26",
    });
  });

  it("keeps an unpriced model's cost unknown rather than free", () => {
    const read = readProcessingTelemetry({
      total_ms: 10,
      operations: [
        {
          operation: "extraction",
          provider: "google",
          model: "gemini-3.1-flash-lite",
          calls: 1,
          input_tokens: 100,
          output_tokens: 10,
          estimated_cost_usd: null,
        },
      ],
      estimated_cost_usd: null,
      cost_complete: false,
      unpriced_models: ["google/gemini-3.1-flash-lite"],
    })!;
    expect(read.steps[0].costUsd).toBeNull();
    expect(read.costUsd).toBeUndefined();
    expect(read.costComplete).toBe(false);
    expect(read.usedFallback).toBe(false);
  });

  it("shows the reasoning effort a registry variant ran at, priced as its wire model", () => {
    const read = readProcessingTelemetry({
      total_ms: 5700,
      operations: [
        {
          operation: "extraction",
          provider: "openai",
          model: "gpt-5.6-luna",
          requested_model: "gpt-5.6-luna-low",
          configured_model: "gpt-5.6-luna-low",
          reasoning_effort: "low",
          fallback: false,
          calls: 1,
          input_tokens: 1000,
          output_tokens: 200,
          estimated_cost_usd: 0.00044,
        },
      ],
      estimated_cost_usd: 0.00044,
      cost_complete: true,
      unpriced_models: [],
    })!;
    expect(read.steps[0]).toMatchObject({
      detail: "openai / gpt-5.6-luna · reasoning low",
      costUsd: 0.00044,
    });
    expect(read.steps[0].fallbackFrom).toBeUndefined();
    expect(read).toMatchObject({
      extractionModel: "gpt-5.6-luna",
      usedFallback: false,
    });
  });

  it("is absent for a document with no record", () => {
    expect(readProcessingTelemetry(null)).toBeNull();
    expect(readProcessingTelemetry(undefined)).toBeNull();
    expect(readProcessingTelemetry({})).toBeNull();
  });
});
