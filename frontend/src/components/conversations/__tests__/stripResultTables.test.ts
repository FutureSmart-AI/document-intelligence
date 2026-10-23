import { describe, expect, it } from "vitest";

import { stripResultTables } from "../stripResultTables";

const ANSWER = `## Contracts Renewing in March

Three contracts in this collection renew in March:

| Vendor | Renews |
| --- | --- |
| Northwind Supply | 2026-03-04 |
| Acme Logistics | 2026-03-19 |

**Note:** Only signed contracts are counted.`;

/** The frame under `ANSWER`: the same two columns, the same two rows. */
const CARD = {
  columns: ["vendor", "renews"],
  rows: [
    { vendor: "Northwind Supply", renews: "2026-03-04" },
    { vendor: "Acme Logistics", renews: "2026-03-19" },
  ],
};

describe("stripResultTables", () => {
  it("drops the table the result card already draws", () => {
    const out = stripResultTables(ANSWER, CARD);
    expect(out).not.toContain("Northwind Supply");
    expect(out).not.toContain("| --- |");
  });

  it("keeps every word of the prose around it", () => {
    const out = stripResultTables(ANSWER, CARD);
    expect(out).toContain("## Contracts Renewing in March");
    expect(out).toContain("renew in March:");
    expect(out).toContain("**Note:** Only signed contracts are counted.");
  });

  it("drops a restated table whose headers were renamed on the way out", () => {
    const renamed = ANSWER.replace(
      "| Vendor | Renews |",
      "| Supplier | Renewal Date |",
    );
    expect(stripResultTables(renamed, CARD)).not.toContain("Acme Logistics");
  });

  it("keeps a summary the answer built over the frame", () => {
    // The answer grouped the rows before writing them out. The card still
    // holds the frame; this table is the reading of it, and is the only place
    // the summary exists.
    const summary = `## Renewals by Quarter

| **Quarter** | **Contracts** |
| --- | --- |
| Q1 | 3 |
| Q2 | 1 |`;
    const out = stripResultTables(summary, CARD);
    expect(out).toContain("| **Quarter** | **Contracts** |");
    expect(out).toContain("| Q1 | 3 |");
  });

  it("keeps the table when nothing is known about the frame", () => {
    expect(stripResultTables(ANSWER)).toContain("Northwind Supply");
  });

  it("leaves an answer that has no table exactly as written", () => {
    const plain = "There are 42 contracts.\n\n- one\n- two";
    expect(stripResultTables(plain, CARD)).toBe(plain);
  });

  it("does not mistake a pipe in a sentence for a table", () => {
    const prose = "Use a | b to split the field.\nIt is not a table.";
    expect(stripResultTables(prose, CARD)).toBe(prose);
  });

  it("drops more than one restated table", () => {
    const two = `First\n\n| a |\n| --- |\n| 1 |\n\nSecond\n\n| a |\n| --- |\n| 2 |\n\nEnd`;
    const out = stripResultTables(two, {
      columns: ["a"],
      rows: [{ a: 1 }, { a: 2 }],
    });
    expect(out).toContain("First");
    expect(out).toContain("Second");
    expect(out).toContain("End");
    expect(out).not.toContain("| 1 |");
    expect(out).not.toContain("| 2 |");
  });
});
