import { describe, expect, it } from "vitest";

import { deriveTrace } from "../deriveTrace";

const base = {
  loading: false,
  answer: "done",
  sourceCount: 0,
};

describe("deriveTrace", () => {
  it("reads the documents before the first chunk arrives", () => {
    expect(
      deriveTrace({ ...base, loading: true, answer: "", sourceCount: 0 }),
    ).toEqual([
      { key: "reading", label: "Reading your documents", state: "active" },
    ]);
  });

  it("opens Writing once text streams", () => {
    const rows = deriveTrace({ ...base, loading: true, answer: "The report" });
    expect(rows.map((row) => [row.key, row.state])).toEqual([
      ["reading", "done"],
      ["writing", "active"],
    ]);
  });

  it("reports a failure as its own row", () => {
    const rows = deriveTrace({ ...base, error: "The collection is paused." });
    expect(rows[rows.length - 1]).toEqual({
      key: "failed",
      label: "Run failed",
      detail: "The collection is paused.",
      state: "failed",
    });
  });

  it("counts the documents the answer cited", () => {
    expect(deriveTrace({ ...base, sourceCount: 3 })[0].label).toBe(
      "Read 3 documents",
    );
    expect(deriveTrace({ ...base, sourceCount: 1 })[0].label).toBe(
      "Read 1 document",
    );
  });

  it("names the chart when the run built one", () => {
    const rows = deriveTrace({ ...base, chartSuggestion: "Pie Chart" });
    expect(rows[0]).toEqual({
      key: "chart",
      label: "Built a chart",
      detail: "Pie Chart",
      state: "done",
    });
  });

  it("reports the timing the run measured", () => {
    expect(deriveTrace({ ...base, latencyMs: 2400 })[0].detail).toBe("2.40s");
    expect(deriveTrace({ ...base, latencyMs: 320 })[0].detail).toBe("320ms");
  });

  it("draws nothing for an answer that reported no facts", () => {
    expect(deriveTrace(base)).toEqual([]);
  });
});
