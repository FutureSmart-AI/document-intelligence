import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DiRunLog from "../DiRunLog";
import { readRunLog } from "../runLog";

const turn = (usage: Record<string, unknown>) =>
  readRunLog({
    data: {
      knowledge: {
        relevant_sources: { tool_name: "rag", data: [{ id: "d1" }] },
      },
      di_run: { query: "which candidates know python?", usage },
    },
  })!;

const USAGE = {
  latency_ms: 8120,
  total_tokens: 21700,
  estimated_cost_usd: 0.0062,
  cost_complete: true,
  unpriced_models: [],
  price_table: "openai-standard-2026-10-26",
  operation_latency_ms: { retrieval_agent: 4200 },
  operations: [
    {
      operation: "retrieval_agent",
      provider: "openai",
      model: "gpt-5.4-mini",
      requested_model: "gpt-5.6-luna",
      fallback: true,
      calls: 3,
      input_tokens: 15000,
      cached_input_tokens: 4000,
      output_tokens: 400,
      latency_ms: 3900,
      estimated_cost_usd: 0.0045,
    },
    {
      operation: "answer",
      provider: "openai",
      model: "gpt-5.6-luna",
      calls: 1,
      input_tokens: 6000,
      output_tokens: 300,
      latency_ms: 2400,
      estimated_cost_usd: 0.0017,
    },
  ],
};

describe("DiRunLog", () => {
  it("lists every operation with its model, fallback, time, tokens and cost", () => {
    render(<DiRunLog log={turn(USAGE)} />);
    fireEvent.click(screen.getByRole("button", { name: /full logs/i }));

    expect(screen.getByText("Retrieval agent")).toBeTruthy();
    expect(screen.getByText("fallback from gpt-5.6-luna")).toBeTruthy();
    // Stage wall time (tool time included) wins over model-call time.
    expect(screen.getByText("4.20s")).toBeTruthy();
    expect(screen.getByText("(4,000 cached)")).toBeTruthy();
    expect(screen.getByText("$0.0045")).toBeTruthy();
    expect(screen.getByText("≈$0.0062")).toBeTruthy();
    expect(screen.getByText(/prices openai-standard-2026-10-26/)).toBeTruthy();
    expect(screen.queryByRole("note")).toBeNull();
    // An ordinary request never claims an accuracy figure.
    expect(screen.queryByText(/accuracy/i)).toBeNull();
  });

  it("says cost is not available for every model instead of showing $0", () => {
    render(
      <DiRunLog
        log={turn({
          ...USAGE,
          estimated_cost_usd: 0.0017,
          cost_complete: false,
          unpriced_models: ["google/gemini-3.1-flash-lite"],
          operations: [
            {
              ...USAGE.operations[0],
              provider: "google",
              model: "gemini-3.1-flash-lite",
              fallback: false,
              requested_model: "gemini-3.1-flash-lite",
              estimated_cost_usd: null,
            },
            USAGE.operations[1],
          ],
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /full logs/i }));

    expect(screen.getByRole("note").textContent).toMatch(
      /Cost not available for every model \(google\/gemini-3\.1-flash-lite\)/,
    );
    expect(screen.getByTitle("No price for this model").textContent).toBe("—");
    expect(screen.getByText("≥$0.0017")).toBeTruthy();
  });
});
