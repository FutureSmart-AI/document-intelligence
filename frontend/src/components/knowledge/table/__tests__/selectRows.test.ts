import { describe, expect, it } from "vitest";

import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";
import type { ColumnSchema } from "@/platform/types/data-view";

import { buildTable } from "../buildTable";
import { filterRows, selectRows, sortRows, statusCounts } from "../selectRows";
import { EMPTY_QUERY, type FilterRule, type TableQuery } from "../types";

/**
 * What the one table shows, and in what order.
 *
 * The search box, the status control, the type control and the filter rules all
 * narrow the same row set, and they used to live on two different screens with
 * two different ideas of a document's type. These tests pin the combined
 * behaviour: the controls compose, an unfilled rule is not a filter, and a row
 * with nothing extracted sorts below every row that has something in both
 * directions rather than pretending to be a small value.
 */

const DOCUMENTS: KnowledgeItem[] = [
  {
    object_id: "a",
    file_name: "Ananya_Rao_Resume.pdf",
    status: "completed",
    content_type: "file",
    created_time: "2026-07-28T10:00:00Z",
    updated_time: "2026-07-28T10:00:00Z",
  },
  {
    object_id: "b",
    file_name: "Marcus_Webb.pdf",
    status: "queued",
    content_type: "file",
    created_time: "2026-07-26T10:00:00Z",
    updated_time: "2026-07-26T10:00:00Z",
  },
  {
    object_id: "c",
    file_name: "careers.example.com/roles",
    status: "failed",
    content_type: "url",
    created_time: "2026-07-30T10:00:00Z",
    updated_time: "2026-07-30T10:00:00Z",
  },
].map((entry) => ({ ...entry, url: null, s3_url: "" }) as KnowledgeItem);

const COLUMNS: ColumnSchema[] = [
  { name: "candidate", original_field: "candidate", data_type: "str" },
  { name: "role", original_field: "role", data_type: "str" },
  { name: "skills", original_field: "skills", data_type: "List[str]" },
  { name: "experience", original_field: "experience", data_type: "int" },
];

const METADATA: Record<string, unknown>[] = [
  {
    object_id: "a",
    candidate: "Ananya Rao",
    role: "Senior Backend Engineer",
    skills: ["Python", "Kubernetes"],
    experience: 7,
  },
  {
    object_id: "b",
    candidate: "Marcus Webb",
    role: "DevOps Engineer",
    skills: ["Kubernetes"],
    experience: 6,
  },
  // The failed crawl: read, but nothing came out of it.
  { object_id: "c", candidate: "", role: "", skills: [], experience: null },
];

const table = buildTable({
  documents: DOCUMENTS,
  metadataColumns: COLUMNS,
  metadataRows: METADATA,
});

const query = (over: Partial<TableQuery> = {}): TableQuery => ({
  ...EMPTY_QUERY,
  ...over,
});

const rule = (over: Partial<FilterRule>): FilterRule => ({
  id: "r1",
  field: "role",
  op: "Contains",
  value: "",
  ...over,
});

const names = (rows: ReturnType<typeof selectRows>) =>
  rows.map((row) => row.objectId);

describe("filterRows", () => {
  it("searches every column, the object id included", () => {
    expect(
      names(
        filterRows(table.rows, table.columns, query({ search: "kubernetes" })),
      ),
    ).toEqual(["a", "b"]);
    expect(
      names(filterRows(table.rows, table.columns, query({ search: "obj" }))),
    ).toEqual([]);
    expect(
      names(filterRows(table.rows, table.columns, query({ search: "c" }))),
    ).toContain("c");
  });

  it("narrows by how the document arrived", () => {
    expect(
      names(filterRows(table.rows, table.columns, query({ type: "Website" }))),
    ).toEqual(["c"]);
  });

  it("collapses every in-flight status into one bucket", () => {
    expect(
      names(
        filterRows(table.rows, table.columns, query({ status: "processing" })),
      ),
    ).toEqual(["b"]);
    expect(
      names(filterRows(table.rows, table.columns, query({ status: "failed" }))),
    ).toEqual(["c"]);
  });

  it("composes the controls rather than letting the last one win", () => {
    expect(
      names(
        filterRows(
          table.rows,
          table.columns,
          query({ search: "kubernetes", status: "completed" }),
        ),
      ),
    ).toEqual(["a"]);
  });

  it("treats a rule with nothing typed in it as no rule at all", () => {
    expect(
      names(
        filterRows(
          table.rows,
          table.columns,
          query({ filters: [rule({ value: "" })] }),
        ),
      ),
    ).toHaveLength(3);
  });

  it("matches any one entry of a list column on Equals", () => {
    expect(
      names(
        filterRows(
          table.rows,
          table.columns,
          query({
            filters: [rule({ field: "skills", op: "Equals", value: "Python" })],
          }),
        ),
      ),
    ).toEqual(["a"]);
  });

  it("finds the documents a field is empty in", () => {
    expect(
      names(
        filterRows(
          table.rows,
          table.columns,
          query({ filters: [rule({ field: "candidate", op: "Is empty" })] }),
        ),
      ),
    ).toEqual(["c"]);
  });

  it("compares numbers as numbers", () => {
    expect(
      names(
        filterRows(
          table.rows,
          table.columns,
          query({
            filters: [
              rule({ field: "experience", op: "Greater than", value: "6" }),
            ],
          }),
        ),
      ),
    ).toEqual(["a"]);
  });

  /**
   * `Is any of` — the operator the facet band writes.
   *
   * It exists because rules are ANDed: two `Equals` rules on one column asked
   * for a role that was two different roles at once and answered with an empty
   * table, which is what a second click in the band used to do.
   */
  describe("Is any of", () => {
    it("keeps every document matching any value in the set", () => {
      expect(
        names(
          filterRows(
            table.rows,
            table.columns,
            query({
              filters: [
                rule({
                  field: "role",
                  op: "Is any of",
                  value: "",
                  values: ["Senior Backend Engineer", "DevOps Engineer"],
                }),
              ],
            }),
          ),
        ),
      ).toEqual(["a", "b"]);
    });

    it("keeps the documents with no value only when asked", () => {
      const withEmpties = (includeEmpty: boolean) =>
        names(
          filterRows(
            table.rows,
            table.columns,
            query({
              filters: [
                rule({
                  field: "role",
                  op: "Is any of",
                  value: "",
                  values: ["Senior Backend Engineer"],
                  includeEmpty,
                }),
              ],
            }),
          ),
        );
      expect(withEmpties(false)).toEqual(["a"]);
      // One rule, not two: "that role, or nothing at all" is one question,
      // and a second `Is empty` rule would be ANDed into answering none.
      expect(withEmpties(true)).toEqual(["a", "c"]);
    });

    it("matches a list column on any one of its entries", () => {
      expect(
        names(
          filterRows(
            table.rows,
            table.columns,
            query({
              filters: [
                rule({
                  field: "skills",
                  op: "Is any of",
                  value: "",
                  values: ["Kubernetes"],
                }),
              ],
            }),
          ),
        ),
      ).toEqual(["a", "b"]);
    });

    it("keeps every row while nothing has been picked yet", () => {
      // An empty set is a rule not finished, the same as a half-typed
      // `Contains` — not a rule that excludes everything.
      expect(
        filterRows(
          table.rows,
          table.columns,
          query({
            filters: [
              rule({ field: "role", op: "Is any of", value: "", values: [] }),
            ],
          }),
        ),
      ).toHaveLength(3);
    });
  });

  it("ignores the rules on one field when asked to", () => {
    const withRule = query({
      filters: [
        rule({ field: "role", op: "Equals", value: "DevOps Engineer" }),
      ],
    });
    expect(names(filterRows(table.rows, table.columns, withRule))).toEqual([
      "b",
    ]);
    // What the facet band does, so its sibling values stay reachable.
    expect(
      names(filterRows(table.rows, table.columns, withRule, "role")),
    ).toHaveLength(3);
  });
});

describe("sortRows", () => {
  it("sorts a numeric column by value and keeps the empties last", () => {
    expect(
      names(
        sortRows(table.rows, table.columns, [{ field: "experience", dir: 1 }]),
      ),
    ).toEqual(["b", "a", "c"]);
    expect(
      names(
        sortRows(table.rows, table.columns, [{ field: "experience", dir: -1 }]),
      ),
    ).toEqual(["a", "b", "c"]);
  });

  it("sorts dates chronologically, not as text", () => {
    expect(
      names(
        sortRows(table.rows, table.columns, [{ field: "created", dir: 1 }]),
      ),
    ).toEqual(["b", "a", "c"]);
  });

  it("uses the second rule only where the first ties", () => {
    expect(
      names(
        sortRows(table.rows, table.columns, [
          { field: "addedvia", dir: 1 },
          { field: "candidate", dir: -1 },
        ]),
      ),
    ).toEqual(["b", "a", "c"]);
  });
});

describe("statusCounts", () => {
  it("counts the whole collection, three buckets and a total", () => {
    expect(statusCounts(table.rows)).toEqual({
      all: 3,
      completed: 1,
      processing: 1,
      failed: 1,
    });
  });
});
