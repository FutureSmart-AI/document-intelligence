import { describe, expect, it } from "vitest";

import { readRunLog } from "../runLog";

/** A live turn, shaped as the final stream event leaves it on the message. */
const live = (over: Record<string, unknown> = {}) => ({
  data: {
    knowledge: {
      relevant_sources: {
        tool_name: "rag",
        data: [
          { id: "d1", title: "priya.pdf" },
          { id: "d2", title: "john.pdf" },
        ],
      },
      // Measured live: the service left `filename` off all thirteen summaries
      // of a run, so the name has to come from the retrieved source beside it.
      summaries: { d1: { summary: "…" }, d2: { summary: "…" } },
      detailed_chunks: {
        d1: [
          { relevance_score: "0.625", search_method: "hybrid_knowledge_base" },
        ],
        d2: [
          { relevance_score: "0.410", search_method: "hybrid_knowledge_base" },
          { relevance_score: "0.380", search_method: "hybrid_knowledge_base" },
        ],
      },
      chart_output: {
        type: "chart_response",
        chart_generated: true,
        chart_suggestion: "Bar Chart",
      },
      ...(over.knowledge as object),
    },
    di_run: {
      query: "which candidates know python?",
      refined_query: "which candidates know python?",
      is_answered: "yes",
      debug_info: { type: "vanilla_rag" },
      usage: {
        total_tokens: 42526,
        service_calls: [
          {
            service: "generate_answer",
            latency_ms: 3670,
            provider: "openai",
            model: "gpt-5.4-mini",
            input_tokens: 18422,
            output_tokens: 85,
            cost_usd: 0.014199,
          },
          { service: "chart", latency_ms: 10906, cost_usd: 0.020497 },
        ],
      },
      ...(over.di_run as object),
    },
  },
});

describe("readRunLog", () => {
  it("logs the run the service reported, step by step", () => {
    const log = readRunLog(live())!;
    expect(log.steps.map((step) => step.key)).toEqual([
      "question",
      "retrieval",
      "answer",
      "chart",
    ]);
    expect(log.runType).toBe("vanilla_rag");
    expect(log.partial).toBe(false);
  });

  it("totals the run from the per-service calls, not from a wall clock", () => {
    const log = readRunLog(live())!;
    expect(log.totals?.latencyMs).toBe(14576);
    expect(log.totals?.totalTokens).toBe(42526);
    expect(log.totals?.costUsd).toBeCloseTo(0.034696, 6);
  });

  it("names the search, its method and the span of scores it matched", () => {
    const retrieval = readRunLog(live())!.steps.find(
      (step) => step.key === "retrieval",
    )!;
    expect(retrieval.label).toBe("Searched the collection (rag)");
    expect(retrieval.detail).toBe(
      "2 documents · hybrid_knowledge_base · scores 0.410–0.625",
    );
    // Best match first, and named by file rather than by document id.
    expect(retrieval.lines?.[0]).toBe("0.625  priya.pdf  (1 chunk)");
    expect(retrieval.lines?.[1]).toBe("0.410  john.pdf  (2 chunks)");
  });

  it("carries each step's own latency, tokens and cost", () => {
    const answer = readRunLog(live())!.steps.find(
      (step) => step.key === "answer",
    )!;
    expect(answer.call).toMatchObject({
      latencyMs: 3670,
      model: "gpt-5.4-mini",
      costUsd: 0.014199,
    });
    expect(answer.detail).toBe("openai / gpt-5.4-mini");
  });

  it("shows the question the service rewrote, next to the one that was asked", () => {
    const log = readRunLog(
      live({ di_run: { refined_query: "Which candidates know Python?" } }),
    )!;
    const question = log.steps[0];
    expect(question.label).toBe("Rewrote the question");
    expect(question.lines).toEqual([
      "asked:   which candidates know python?",
      "used:    Which candidates know Python?",
    ]);
  });

  it("flags a thin-evidence run without calling the answer a failure", () => {
    // Measured live: a two-part question whose second half had no evidence
    // came back `is_answered: "no"` with a good answer to the first half.
    const log = readRunLog(live({ di_run: { is_answered: "no" } }))!;
    const answer = log.steps.find((step) => step.key === "answer")!;
    expect(answer.label).toBe("Wrote the answer");
    expect(answer.state).toBe("flagged");
    expect(answer.lines?.join(" ")).toMatch(/not fully covered/);
  });

  it("logs why the chart step decided against a chart", () => {
    const log = readRunLog(
      live({
        knowledge: {
          chart_output: {
            type: "direct_response",
            graph_generated: false,
            message: "The data is a single value.\nNothing to plot.",
          },
        },
      }),
    )!;
    const chart = log.steps.find((step) => step.key === "chart")!;
    expect(chart.label).toBe("Decided against a chart");
    expect(chart.state).toBe("skipped");
    expect(chart.detail).toBe("The data is a single value.");
  });

  it("says a stored turn has no timings rather than reporting zero", () => {
    const log = readRunLog({
      data: {
        knowledge: {
          relevant_sources: { tool_name: "rag", data: [{ id: "d1" }] },
        },
        di_run: { model: "gpt-5.4-mini", is_answered: true, stored: true },
      },
    })!;
    expect(log.partial).toBe(true);
    expect(log.totals).toBeUndefined();
    expect(
      log.steps.find((step) => step.key === "answer")?.call,
    ).toBeUndefined();
  });

  it("logs nothing for a turn the service said nothing about", () => {
    expect(readRunLog({ message: "hi" })).toBeNull();
    expect(readRunLog({ data: {} })).toBeNull();
  });
});
