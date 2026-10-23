import { describe, expect, it } from "vitest";

import type { TableColumn } from "@/components/knowledge/table/types";

import { locateValue } from "../locate";

/**
 * "Show in text" beside a field value — the source link the ticket asked for.
 * The text is a synthetic freight invoice with a rate of 0.18 and a distance of
 * 450, the shape where a substring match would pick the wrong number.
 */
const SECTIONS = [
  {
    id: "a",
    text: "Invoice number **NSF-7710**\n**DISTANCE**\n450 miles\n**FUEL RATE**\n£0.18 per mile\nIssued 2018.",
  },
  {
    id: "b",
    text: "Fuel charge £81.00 · 1,234 pallets\nInspection date 07 March 2026",
  },
];

const column = (vtype: TableColumn["vtype"]): TableColumn => ({
  key: "field",
  label: "Field",
  group: "fields",
  vtype,
  type: "str",
});

const find = (vtype: TableColumn["vtype"], value: unknown) => {
  const match = locateValue(SECTIONS, column(vtype), value);
  if (!match) return null;
  const text = SECTIONS.find((section) => section.id === match.sectionId)!.text;
  return { section: match.sectionId, text: text.slice(match.start, match.end) };
};

describe("locateValue", () => {
  it("finds text case-insensitively", () => {
    expect(find("text", "nsf-7710")).toEqual({
      section: "a",
      text: "NSF-7710",
    });
  });

  it("finds a number as a whole number, never inside a longer one", () => {
    expect(find("num", 450)).toEqual({ section: "a", text: "450" });
    expect(find("num", 0.18)).toEqual({ section: "a", text: "0.18" });
    // "18" is inside 0.18 and 2018 — neither is the number 18.
    expect(find("num", 18)).toBeNull();
  });

  it("finds a number the way documents write it", () => {
    // The service returns 81.0, which JavaScript holds as 81.
    expect(find("num", 81)).toEqual({ section: "b", text: "81.00" });
    expect(find("num", 1234)).toEqual({ section: "b", text: "1,234" });
  });

  it("finds a date written out in words", () => {
    expect(find("date", "2026-03-07")).toEqual({
      section: "b",
      text: "07 March 2026",
    });
  });

  it("offers nothing for a value the text does not contain", () => {
    expect(find("text", "GBP")).toBeNull();
    expect(find("text", "x")).toBeNull();
    expect(find("text", true)).toBeNull();
    expect(find("text", ["a", "b"])).toBeNull();
    expect(find("text", null)).toBeNull();
  });
});
