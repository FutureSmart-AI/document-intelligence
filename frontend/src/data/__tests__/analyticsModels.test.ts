import { describe, expect, it } from "vitest";

import type { Scorecard } from "@/platform/analytics/types";

import {
  modelRows,
  stepModelRows,
  stepNames,
  stepVerdict,
  superlatives,
} from "../analyticsModels";

/**
 * The Model performance tab is arithmetic over the scorecard's per-(step, model)
 * rows, so this is where its claims are pinned — especially the ones the mock
 * asked for and the data cannot support.
 */

const node = (
  step: string,
  model: string,
  provider: string,
  over: Partial<Scorecard["perf"]["by_node"][number]> = {},
) => ({
  node_name: step,
  model,
  provider,
  queries: 10,
  total_calls: 10,
  errors: 0,
  avg_latency_ms: 200,
  p50_latency_ms: 180,
  p95_latency_ms: 400,
  avg_latency_s: 0.2,
  p50_latency_s: 0.18,
  p95_latency_s: 0.4,
  total_tokens: 1000,
  input_tokens: 900,
  output_tokens: 100,
  total_cost: 0.01,
  ...over,
});

const scorecard = (over: Partial<Scorecard>): Scorecard =>
  ({
    agent_id: "kb-1",
    name: null,
    period: "week",
    since: "",
    until: "",
    previous_since: "",
    usage: {
      total_queries: 20,
      unique_users: 1,
      widget_queries: 0,
      per_day: [],
    },
    quality: {
      answered: 18,
      unanswered: 2,
      success_rate: 90,
      failed_api_requests: 0,
      no_answer: 2,
      budget_exhausted: 0,
      feedback: null,
    },
    perf: { avg_ms: 200, p50_ms: 180, p95_ms: 400, by_node: [], slowest: [] },
    cost: {
      total_cost: 0.02,
      avg_cost_per_query: 0.001,
      total_tokens: 2000,
      input_tokens: 1800,
      output_tokens: 200,
      cost_complete: true,
      by_model: [],
      by_node: [],
      most_expensive: [],
    },
    ...over,
  }) as Scorecard;

const TWO_MODELS = scorecard({
  perf: {
    avg_ms: 200,
    p50_ms: 180,
    p95_ms: 400,
    slowest: [],
    by_node: [
      // Sonnet ran on both steps; it is slower on "compose".
      node("understand", "sonnet", "anthropic", {
        queries: 12,
        total_calls: 12,
        avg_latency_ms: 200,
        p95_latency_ms: 400,
        total_cost: 0.006,
      }),
      node("compose", "sonnet", "anthropic", {
        queries: 12,
        total_calls: 6,
        avg_latency_ms: 800,
        p95_latency_ms: 1600,
        total_cost: 0.006,
        errors: 3,
      }),
      node("understand", "haiku", "anthropic", {
        queries: 8,
        total_calls: 8,
        avg_latency_ms: 100,
        p50_latency_ms: 90,
        p95_latency_ms: 150,
        total_cost: 0.001,
      }),
    ],
  },
});

describe("modelRows", () => {
  const rows = modelRows(TWO_MODELS);

  it("is one row per model, busiest first", () => {
    expect(rows.map((row) => row.model)).toEqual(["sonnet", "haiku"]);
  });

  it("counts a question once even when it used several steps", () => {
    // Sonnet ran on two steps for the same 12 questions: 12 answers, not 24.
    expect(rows[0].answers).toBe(12);
    expect(rows[0].calls).toBe(18);
  });

  it("weights average latency by calls, not by step count", () => {
    // (200×12 + 800×6) / 18 = 400
    expect(rows[0].avgMs).toBeCloseTo(400);
  });

  it("reports the slowest step's p95 rather than averaging percentiles", () => {
    // The mean of two p95s is not a p95, so the row names a real step.
    expect(rows[0].slowest).toEqual({ step: "compose", p95Ms: 1600 });
  });

  it("meters errors against calls, and says so as error-free calls", () => {
    // 3 of sonnet's 18 calls errored.
    expect(rows[0].errorFreeRate).toBeCloseTo((15 / 18) * 100);
    expect(rows[1].errorFreeRate).toBe(100);
  });

  it("divides cost by answers, and tokens by calls", () => {
    expect(rows[0].totalCost).toBeCloseTo(0.012);
    expect(rows[0].costPerAnswer).toBeCloseTo(0.012 / 12);
    expect(rows[0].tokensInPerCall).toBeCloseTo(1800 / 18);
  });

  it("shares add up to the models' own total, never past 100%", () => {
    const total = rows.reduce((sum, row) => sum + row.freq, 0);
    expect(total).toBeCloseTo(100);
  });

  it("lists the steps a model ran on", () => {
    expect(rows[0].steps).toEqual(["understand", "compose"]);
    expect(rows[1].steps).toEqual(["understand"]);
  });

  it("still lists a model when only the cost roll-up mentions it", () => {
    // Live windows come back with `by_node: []` and a populated `by_model`;
    // the table has to survive that rather than reading as "no models".
    const rollupOnly = modelRows(
      scorecard({
        cost: {
          ...scorecard({}).cost,
          by_model: [
            {
              provider: "openai",
              model: "gpt-5.6-terra",
              queries: 4,
              cost: 0.01,
            },
          ],
        },
      }),
    );
    expect(rollupOnly).toHaveLength(1);
    expect(rollupOnly[0]).toMatchObject({
      model: "gpt-5.6-terra",
      answers: 4,
      // Nothing was metered per step, so these stay unknown rather than zero.
      avgMs: null,
      slowest: null,
      errorFreeRate: null,
      steps: [],
    });
    expect(rollupOnly[0].costPerAnswer).toBeCloseTo(0.0025);
  });

  it("is empty when the window recorded no model at all", () => {
    expect(modelRows(scorecard({}))).toEqual([]);
    expect(modelRows(undefined)).toEqual([]);
  });
});

describe("stepNames and stepModelRows", () => {
  it("lists the steps in the order the service reported them", () => {
    expect(stepNames(TWO_MODELS)).toEqual(["understand", "compose"]);
  });

  it("keeps the real percentiles per step, and shares within that step", () => {
    const rows = stepModelRows(TWO_MODELS, "understand");
    expect(rows.map((row) => row.model)).toEqual(["sonnet", "haiku"]);
    expect(rows[0]).toMatchObject({ runs: 12, p50Ms: 180, p95Ms: 400 });
    expect(rows[0].freq).toBeCloseTo((12 / 20) * 100);
    expect(rows[1].costPerRun).toBeCloseTo(0.001 / 8);
  });

  it("is empty for a step nothing ran on", () => {
    expect(stepModelRows(TWO_MODELS, "check")).toEqual([]);
  });
});

describe("superlatives", () => {
  const rows = modelRows(TWO_MODELS);
  const best = superlatives(rows);

  it("picks the fastest and cheapest by their own measures", () => {
    expect(best.fastest?.model).toBe("haiku");
    expect(best.cheapest?.model).toBe("haiku");
  });

  it("names no most-reliable model when they all did equally well", () => {
    // One model, or a tie, is not "the most reliable" — the card says so
    // instead of crowning an arbitrary row.
    const tied = modelRows(
      scorecard({
        perf: {
          ...scorecard({}).perf,
          by_node: [
            node("understand", "a", "openai"),
            node("understand", "b", "openai"),
          ],
        },
      }),
    );
    expect(superlatives(tied).mostReliable).toBeUndefined();
    expect(best.mostReliable?.model).toBe("haiku");
  });
});

describe("stepVerdict", () => {
  const rows = stepModelRows(TWO_MODELS, "understand");

  it("labels the extremes and leaves the middle alone", () => {
    expect(stepVerdict(rows[1], rows)).toEqual({
      label: "Fastest",
      tone: "success",
    });
    expect(stepVerdict(rows[0], rows)).toEqual({
      label: "Slowest",
      tone: "warn",
    });
  });

  it("has no verdict when there is nothing to compare against", () => {
    expect(stepVerdict(rows[0], [rows[0]])).toBeNull();
  });
});
