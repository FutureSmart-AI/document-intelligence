import { describe, expect, it } from "vitest";

import { readChartDataframe } from "../chartDataframe";

const message = (dataframe: unknown) => ({
  role: "assistant",
  data: { knowledge: { chart_output: { dataframe } } },
});

describe("readChartDataframe", () => {
  it("reads the split orientation pandas emits", () => {
    const frame = readChartDataframe(
      message({
        columns: ["name", "years"],
        data: [
          ["Ada", 8],
          ["Grace", 12],
        ],
        shape: [2, 2],
      }),
    );
    expect(frame).toEqual({
      columns: ["name", "years"],
      rows: [
        { name: "Ada", years: 8 },
        { name: "Grace", years: 12 },
      ],
      totalRows: 2,
    });
  });

  it("reads the records orientation too", () => {
    const frame = readChartDataframe(
      message({
        columns: ["name", "years"],
        data: [{ years: 8, name: "Ada" }],
      }),
    );
    // `columns` fixes the order, whatever order the keys arrived in.
    expect(frame?.columns).toEqual(["name", "years"]);
    expect(frame?.rows).toEqual([{ years: 8, name: "Ada" }]);
  });

  it("keeps a column the frame's own list left out", () => {
    const frame = readChartDataframe(
      message({ columns: ["name"], data: [{ name: "Ada", years: 8 }] }),
    );
    expect(frame?.columns).toEqual(["name", "years"]);
  });

  it("refuses value arrays with no column names to show them under", () => {
    expect(readChartDataframe(message({ data: [["Ada", 8]] }))).toBeNull();
  });

  it("parses a payload the session route handed back as a string", () => {
    const frame = readChartDataframe({
      data: JSON.stringify({
        knowledge: {
          chart_output: { dataframe: { columns: ["a"], data: [["1"]] } },
        },
      }),
    });
    expect(frame?.rows).toEqual([{ a: "1" }]);
  });

  it("returns nothing for an answer with no frame behind it", () => {
    expect(
      readChartDataframe({ role: "assistant", response: "42" }),
    ).toBeNull();
    expect(
      readChartDataframe(message({ columns: ["a"], data: [] })),
    ).toBeNull();
    expect(readChartDataframe(message(null))).toBeNull();
  });
});
