import { describe, expect, it } from "vitest";

import { formatStat } from "../format";

/**
 * The histogram's stat cells and bucket labels used to round to one decimal,
 * so a column of per-mile rates read "Lowest 0 · Highest 0.2" — the same kind
 * of collapse the ticket blamed on the field type.
 */
describe("formatStat", () => {
  it("keeps the digits a small decimal is made of", () => {
    expect(formatStat(0.18)).toBe("0.18");
    expect(formatStat(0.04)).toBe("0.04");
    expect(formatStat(0.12345)).toBe("0.12");
    expect(formatStat(-0.004)).toBe("-0.004");
  });

  it("prints whole numbers and money without trailing zeros", () => {
    expect(formatStat(6)).toBe("6");
    expect(formatStat(6.25)).toBe("6.25");
    expect(formatStat(6.5)).toBe("6.5");
    expect(formatStat(1234.567)).toBe("1234.57");
    expect(formatStat(0)).toBe("0");
  });

  it("prints a dash when there is no number", () => {
    expect(formatStat(null)).toBe("—");
    expect(formatStat(Number.NaN)).toBe("—");
  });
});
