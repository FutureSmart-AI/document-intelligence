import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DiResultTable from "../DiResultTable";

const ROWS = [
  {
    id: "7153d846-8a48-4457-b2b0-f1228a2678bd",
    vendor: "Northwind Supply",
    renews_at: "2026-03-04T00:00:00",
    contracts: 12,
  },
];

const COLUMNS = ["id", "vendor", "renews_at", "contracts"];

const cell = (text: string) => screen.getByText(text);

describe("DiResultTable", () => {
  it("sets ids, timestamps and numbers in mono, prose in the body face", () => {
    render(
      <DiResultTable rows={ROWS} columns={COLUMNS} totalRows={1} query="" />,
    );
    expect(cell(ROWS[0].id)).toHaveClass("font-mono");
    expect(cell("2026-03-04T00:00:00")).toHaveClass("font-mono");
    expect(cell("12")).toHaveClass("font-mono");
    expect(cell("Northwind Supply")).not.toHaveClass("font-mono");
  });

  it("greys the row's identity and its timestamps, and left-aligns every cell", () => {
    render(
      <DiResultTable rows={ROWS} columns={COLUMNS} totalRows={1} query="" />,
    );
    expect(cell(ROWS[0].id)).toHaveClass("text-ink-3");
    expect(cell("2026-03-04T00:00:00")).toHaveClass("text-ink-3");
    expect(cell("Northwind Supply")).toHaveClass("text-ink-2");
    // Numbers are not pushed to the right edge — the artifact reads left.
    expect(cell("12")).toHaveClass("text-left");
    expect(cell("12")).not.toHaveClass("text-right");
  });

  it("counts the rows the artifact's way", () => {
    render(
      <DiResultTable rows={ROWS} columns={COLUMNS} totalRows={1} query="" />,
    );
    expect(screen.getByText("Showing 1–1 of 1")).toBeInTheDocument();
  });

  it("says what a filter narrowed away", () => {
    render(
      <DiResultTable
        rows={ROWS}
        columns={COLUMNS}
        totalRows={9}
        query="northwind"
      />,
    );
    expect(
      screen.getByText("Showing 1–1 of 1 (filtered from 9)"),
    ).toBeInTheDocument();
  });
});
