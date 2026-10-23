import { describe, expect, it } from "vitest";

import {
  blankDraft,
  draftFromSuggestion,
  draftsFromCatalog,
  instructionsFrom,
  isUnfinished,
  mergeDrafts,
  slugify,
  toNewFields,
  toPreviewFields,
  validateDrafts,
  type FieldDraft,
} from "../fieldDraft";

const requiresSchema = (fieldType: string) =>
  fieldType === "dict" || fieldType === "List[dict]";

const draft = (over: Partial<FieldDraft> = {}): FieldDraft => ({
  id: "x",
  name: "field",
  // Non-blank by default: a blank description is itself a validation failure,
  // so a fixture that left it empty would fail every unrelated spec.
  desc: "What it captures.",
  fieldType: "",
  origin: "custom",
  ...over,
});

describe("slugify", () => {
  it("makes a name the service will accept", () => {
    expect(slugify("Candidate Name")).toBe("candidate_name");
    expect(slugify("  total/amount  ")).toBe("total_amount");
    expect(slugify("__leading")).toBe("leading");
  });

  /**
   * A trailing underscore survives while it is being typed and is trimmed once
   * the reader has finished — `total_` has to be able to become `total_amount`,
   * and rewriting what somebody typed after they look away is the other way to
   * get this wrong.
   */
  it("keeps a trailing underscore mid-word and drops it after", () => {
    expect(slugify("total_", true)).toBe("total_");
    expect(slugify("total_")).toBe("total");
  });
});

describe("drafts from the service", () => {
  /** A template's fields arrive typed — nothing here re-derives a type. */
  it("carries a catalogue field's type, description and columns across", () => {
    const [simple, table] = draftsFromCatalog(
      [
        {
          field_name: "candidate_name",
          description: "Full name of the candidate",
          field_type: "str",
          field_type_label: "Text",
          level: "document",
        },
        {
          field_name: "work_history",
          description: "One row per job.",
          field_type: "List[dict]",
          field_type_label: "Table",
          schema: { company_name: "str", start_date: "date" },
        },
      ],
      "template",
    );

    expect(simple).toMatchObject({
      name: "candidate_name",
      desc: "Full name of the candidate",
      fieldType: "str",
      origin: "template",
    });
    expect(simple.level).toBe("document");
    expect(table.schema).toEqual({ company_name: "str", start_date: "date" });
  });

  /**
   * A field the service named but did not describe still has to be creatable:
   * the route refuses a description-less field, and a blank box on a row nobody
   * typed would block the step for no reason the reader can see.
   */
  it("fills in a description the service left out", () => {
    const [described, bare] = draftsFromCatalog(
      [
        { field_name: "candidate_name", description: "Full name." },
        { field_name: "notice_period" },
      ],
      "proposed",
    );

    expect(described.desc).toBe("Full name.");
    expect(bare.desc.trim()).not.toBe("");
    expect(bare.desc).toContain("notice period");
  });

  it("marks a field found on the sample document as one", () => {
    const row = draftFromSuggestion({
      field_name: "linkedin_url",
      description: "Profile link.",
      field_type: "str",
      reason: "Present on the document but not requested",
    });

    expect(row).toMatchObject({
      name: "linkedin_url",
      fieldType: "str",
      origin: "suggested",
      why: "Present on the document but not requested",
    });
  });
});

describe("mergeDrafts", () => {
  /**
   * The schema call can propose a field a previously picked template already
   * published, and the backend does not reject a duplicate `field_name` — it
   * would simply create the field twice.
   */
  it("never adds a name the board already has", () => {
    const merged = mergeDrafts(
      [draft({ id: "a", name: "candidate_name", origin: "template" })],
      [
        draft({ id: "b", name: "candidate_name", origin: "proposed" }),
        draft({ id: "c", name: "years_worked", origin: "proposed" }),
      ],
    );

    expect(merged.map((row) => row.name)).toEqual([
      "candidate_name",
      "years_worked",
    ]);
    // The existing row wins: it may have been edited already.
    expect(merged[0].origin).toBe("template");
  });

  it("drops an incoming field with no name at all", () => {
    const merged = mergeDrafts([], [draft({ id: "a", name: "" })]);
    expect(merged).toEqual([]);
  });
});

describe("validateDrafts", () => {
  /**
   * The route answers `400` for a new field with no description — an omitted
   * one and `""` alike — and applies none of the other fields when it does. So
   * one blank box here would be a create that produces an empty collection,
   * which is why the row is left out rather than sent. The message says that,
   * and `isUnfinished` marks it as the kind that does not block the step.
   */
  it("says a blank description leaves the row out", () => {
    const blank = draft({ id: "a", desc: "   " });
    expect(validateDrafts([blank]).a).toBe(
      "Say what this field should capture, or it is left out.",
    );
    expect(isUnfinished(blank)).toBe(true);
  });

  it("says the same about a row with no name", () => {
    const blank = draft({ id: "a", name: "" });
    expect(validateDrafts([blank]).a).toBe(
      "Name this field, or it is left out.",
    );
    expect(isUnfinished(blank)).toBe(true);
  });

  /** A field somebody did write is a conflict to fix, not a row to drop. */
  it("keeps a real conflict blocking", () => {
    const rows = [
      draft({ id: "a", name: "total", desc: "The total." }),
      draft({ id: "b", name: "total", desc: "Also the total." }),
    ];
    expect(rows.every((row) => !isUnfinished(row))).toBe(true);
  });

  /** The backend accepts two fields with one name; nothing else catches it. */
  it("marks both halves of a duplicate", () => {
    const problems = validateDrafts([
      draft({ id: "a", name: "total" }),
      draft({ id: "b", name: "total" }),
    ]);

    expect(problems.a).toBe("Another field is already called total.");
    expect(problems.b).toBe(problems.a);
  });

  /**
   * A table with no columns is a field the service will happily create and
   * nothing will ever fill, so the board refuses it. Branching on the
   * catalogue's `requires_schema` rather than a hardcoded pair of type names is
   * the point — the caller passes the flag in.
   */
  it("refuses a table with no columns", () => {
    const problems = validateDrafts(
      [draft({ id: "a", name: "work_history", fieldType: "List[dict]" })],
      requiresSchema,
    );

    expect(problems.a).toBe(
      "This type is made of sub-fields — add at least one.",
    );
  });

  it("refuses a column with no name", () => {
    const problems = validateDrafts(
      [
        draft({
          id: "a",
          name: "work_history",
          fieldType: "List[dict]",
          schema: { "": "str" },
        }),
      ],
      requiresSchema,
    );

    expect(problems.a).toBe("Every sub-field needs a name.");
  });

  it("accepts a table that has its columns", () => {
    const problems = validateDrafts(
      [
        draft({
          id: "a",
          name: "work_history",
          fieldType: "List[dict]",
          schema: { company_name: "str" },
        }),
      ],
      requiresSchema,
    );

    expect(problems).toEqual({});
  });

  /**
   * Every row on the board is going to be created — there is no "present but
   * switched off" state any more — so a nameless one is a problem rather than
   * something to skip over.
   */
  it("marks a row with no name", () => {
    const problems = validateDrafts([draft({ id: "a", name: "" })]);

    expect(problems).toEqual({ a: "Name this field, or it is left out." });
  });
});

describe("toNewFields", () => {
  /**
   * What leaves this board is what gets built. Nothing is re-derived from
   * prose on the way out and nothing is added that was not on screen.
   */
  it("sends every row, named, described and typed as it stands", () => {
    expect(
      toNewFields([
        draft({ id: "a", name: "Candidate Name", desc: " Full name. " }),
        draft({
          id: "b",
          name: "years",
          desc: "Years worked.",
          fieldType: "int",
        }),
        draft({
          id: "d",
          name: "chunk_topic",
          desc: "This section's topic.",
          level: "chunk",
          origin: "proposed",
        }),
      ]),
    ).toEqual([
      { field_name: "candidate_name", description: "Full name." },
      { field_name: "years", description: "Years worked.", field_type: "int" },
      {
        field_name: "chunk_topic",
        description: "This section's topic.",
        level: "chunk",
      },
    ]);
  });

  /** An empty schema object would create a table with no columns. */
  it("omits an empty column set", () => {
    expect(
      toNewFields([
        draft({
          id: "a",
          name: "rows",
          desc: "A group.",
          fieldType: "dict",
          schema: {},
        }),
      ]),
    ).toEqual([
      { field_name: "rows", description: "A group.", field_type: "dict" },
    ]);
  });

  it("is the same list the preview call is told about", () => {
    const rows = [
      draft({
        id: "a",
        name: "total",
        desc: "The amount due.",
        fieldType: "float",
      }),
    ];

    expect(toPreviewFields(rows)).toEqual([
      {
        field_name: "total",
        description: "The amount due.",
        field_type: "float",
      },
    ]);
  });
});

describe("blankDraft", () => {
  it("never collides with a name already on the board", () => {
    const first = blankDraft([]);
    const second = blankDraft([first]);

    expect(first.name).toBe("new_field");
    expect(second.name).toBe("new_field_2");
  });

  /**
   * A hand-added row arrives typed, so the only thing left to do on it is say
   * what it captures — and the type is the catalogue's, not a guess.
   */
  it("starts as text, or as whatever the catalogue calls text", () => {
    expect(blankDraft([]).fieldType).toBe("str");
    expect(blankDraft([], "string").fieldType).toBe("string");
  });
});

describe("instructionsFrom", () => {
  /**
   * The board is sent as fields now, not folded back into prose, so this is
   * only the person's own wording. An empty box has to be absent rather than
   * an empty string: the schema call treats the two differently.
   */
  it("is the wording, or nothing at all", () => {
    expect(instructionsFrom("  the person's name  ")).toBe("the person's name");
    expect(instructionsFrom("   ")).toBeUndefined();
    expect(instructionsFrom("")).toBeUndefined();
  });
});
