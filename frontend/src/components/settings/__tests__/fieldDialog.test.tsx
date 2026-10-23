import { describe, expect, it } from "vitest";

import {
  isGroupType,
  isListType,
  suggestWireType,
} from "../fields/fieldTypeLore";
import { parseImport } from "../fields/ImportFieldsModal";

/**
 * The two pieces of the fields dialog that decide what reaches the service.
 *
 * The type suggestion is a preview — on create the service resolves the type
 * from the description itself — but it is the preview the table cell and the
 * "most values to keep" box are drawn from, so the words it turns on are worth
 * pinning. The import parser matters for the opposite reason: what it drops is
 * what stops a whole import 400ing over one blank description.
 */

describe("suggestWireType", () => {
  it("reads a plain description as text", () => {
    expect(suggestWireType("Full name of the candidate.", [])).toBe("str");
  });

  it("keeps every value when the description asks for all of them", () => {
    expect(
      suggestWireType("Every technical skill listed anywhere in the CV.", []),
    ).toBe("List[str]");
    expect(isListType("List[str]")).toBe(true);
  });

  it("reads a count as a number", () => {
    expect(suggestWireType("Total years of professional experience.", [])).toBe(
      "int",
    );
  });

  /**
   * Money and rates were read as `int`, and a field created as `int` keeps
   * whole numbers only — `fuel_rate_per_mile` came back as 0 instead of 0.18.
   */
  it("reads money, rates and percentages as a decimal number", () => {
    expect(suggestWireType("Fuel rate per mile.", [])).toBe("float");
    expect(suggestWireType("Total fuel cost for the trip.", [])).toBe("float");
    expect(suggestWireType("The invoice amount.", [])).toBe("float");
    expect(suggestWireType("Unit price of the item.", [])).toBe("float");
    expect(suggestWireType("Discount percentage applied.", [])).toBe("float");
  });

  /** "rate" as a bare substring also fires on separate, corporate, accurate. */
  it("does not find a rate inside another word", () => {
    expect(suggestWireType("The corporate name, kept separate.", [])).toBe(
      "str",
    );
  });

  /**
   * "the candidate's full name" typed as a Date when `date` was matched as a
   * bare substring — candidate, update, mandate and validate all contain it.
   */
  it("does not find a date inside another word", () => {
    expect(suggestWireType("The candidate's full name.", [])).toBe("str");
    expect(suggestWireType("The date the contract was signed.", [])).toBe(
      "date",
    );
  });

  /** A yes-no question may carry a date word; the yes-no reading wins. */
  it("reads a yes-or-no question as yes / no", () => {
    expect(suggestWireType("Whether they can start immediately.", [])).toBe(
      "bool",
    );
  });

  it("refines plain text with the accepted values", () => {
    expect(suggestWireType("The result.", ["yes", "no"])).toBe("bool");
    expect(suggestWireType("The result.", ["10", "20", "30"])).toBe("int");
    expect(suggestWireType("The result.", ["0.18", "0.2"])).toBe("float");
    expect(suggestWireType("The result.", ["B.Tech", "M.Tech"])).toBe("str");
  });

  it("opens the columns box only for the types made of parts", () => {
    const types = [
      { field_type: "str", field_type_label: "Text", requires_schema: false },
      {
        field_type: "List[dict]",
        field_type_label: "Table",
        requires_schema: true,
      },
    ];
    expect(isGroupType(types, "List[dict]")).toBe(true);
    expect(isGroupType(types, "str")).toBe(false);
  });
});

describe("parseImport", () => {
  it("reads the document / section shape", () => {
    const { fields, error } = parseImport(
      JSON.stringify({
        document_fields: [
          { field_name: "vendor", description: "Who issued the invoice." },
        ],
        section_fields: [
          { field_name: "clause", description: "The clause discussed here." },
        ],
      }),
    );

    expect(error).toBeNull();
    expect(fields).toHaveLength(2);
    expect(fields[0].level).toBe("document");
    expect(fields[1].level).toBe("chunk");
  });

  /** No type in the file means the service resolves it, as it does elsewhere. */
  it("leaves the type off when the file does not give one", () => {
    const { fields } = parseImport(
      '[{"field_name": "vendor", "description": "Who issued it."}]',
    );

    expect(fields[0]).not.toHaveProperty("field_type");
  });

  it("keeps a type the file does give", () => {
    const { fields } = parseImport(
      '[{"field_name": "total", "description": "Invoice total.", "field_type": "float"}]',
    );

    expect(fields[0].field_type).toBe("float");
  });

  /**
   * The service applies an enrichment call atomically and refuses the whole of
   * it over one missing description, so a nameless or undescribed row is
   * dropped here instead of taking the rest of the import down with it.
   */
  it("drops a field with no name or no description", () => {
    const { fields, error } = parseImport(
      JSON.stringify({
        fields: [
          { field_name: "vendor", description: "Who issued the invoice." },
          { field_name: "blank" },
          { description: "No name at all." },
        ],
      }),
    );

    expect(error).toBeNull();
    expect(fields.map((field) => field.field_name)).toEqual(["vendor"]);
  });

  it("says so when the text is not JSON, and when it holds no fields", () => {
    expect(parseImport("not json").error).toBe("That is not valid JSON.");
    expect(parseImport("{}").error).toMatch(/No fields to import/);
  });
});
