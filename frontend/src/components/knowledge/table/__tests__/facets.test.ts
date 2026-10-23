import { describe, expect, it } from "vitest";

import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";
import type { ColumnSchema } from "@/platform/types/data-view";

import { buildTable } from "../buildTable";
import {
  coverageItems,
  facetableColumns,
  facetCounts,
  facetValueLabel,
  histogram,
} from "../facets";
import { EMPTY_QUERY, type TableQuery } from "../types";

/**
 * The band that used to be the Explore screen.
 *
 * Three behaviours are what make it usable rather than decorative: a list
 * column contributes one count per entry (so "Kubernetes: 2" means two
 * documents mention it), the documents where the field is empty get a bucket of
 * their own rather than vanishing, and the counts ignore the rules on the field
 * being grouped by — otherwise picking a value would collapse the band to that
 * one value and there would be nothing left to pivot to.
 */

const DOCUMENTS: KnowledgeItem[] = [
  { object_id: "a", file_name: "a.pdf", status: "completed" },
  { object_id: "b", file_name: "b.pdf", status: "completed" },
  { object_id: "c", file_name: "c.pdf", status: "failed" },
].map(
  (entry) =>
    ({
      ...entry,
      content_type: "file",
      url: null,
      s3_url: "",
      created_time: "2026-07-28T10:00:00Z",
      updated_time: "2026-07-28T10:00:00Z",
    }) as KnowledgeItem,
);

const COLUMNS: ColumnSchema[] = [
  { name: "role", original_field: "role", data_type: "str" },
  { name: "skills", original_field: "skills", data_type: "List[str]" },
  { name: "experience", original_field: "experience", data_type: "int" },
  { name: "summary", original_field: "summary", data_type: "str" },
];

const METADATA: Record<string, unknown>[] = [
  {
    object_id: "a",
    role: "ML Engineer",
    skills: ["Python", "Kubernetes"],
    experience: 7,
    summary:
      "A long unique paragraph about this first candidate that nobody would group by",
  },
  {
    object_id: "b",
    role: "ML Engineer",
    skills: ["Kubernetes"],
    experience: 5,
    summary:
      "A long unique paragraph about this second candidate that nobody would group by",
  },
  {
    object_id: "c",
    role: "",
    skills: [],
    experience: null,
    summary:
      "A long unique paragraph about this third candidate that nobody would group by",
  },
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

describe("facetCounts", () => {
  it("counts a category and gives the empties a bucket of their own", () => {
    const result = facetCounts(table.rows, table.columns, "role", query());
    expect(result.base).toBe(3);
    expect(result.values).toEqual([
      { value: "ML Engineer", missing: false, count: 2 },
      { value: "", missing: true, count: 1 },
    ]);
    expect(facetValueLabel(result.values[1])).toBe("(no value)");
  });

  it("counts a list column once per entry", () => {
    const result = facetCounts(table.rows, table.columns, "skills", query());
    expect(result.values).toEqual([
      { value: "Kubernetes", missing: false, count: 2 },
      { value: "Python", missing: false, count: 1 },
      { value: "", missing: true, count: 1 },
    ]);
  });

  it("keeps the sibling values reachable once one is selected", () => {
    const selected = query({
      filters: [
        { id: "r1", field: "role", op: "Equals", value: "ML Engineer" },
      ],
    });
    const result = facetCounts(table.rows, table.columns, "role", selected);
    // Both buckets are still there — the selection did not eat the band.
    expect(result.values.map((entry) => entry.count)).toEqual([2, 1]);

    // A different field, though, is narrowed by that rule.
    const skills = facetCounts(table.rows, table.columns, "skills", selected);
    expect(skills.base).toBe(2);
  });

  it("narrows the counts by the search box and the status control", () => {
    const result = facetCounts(
      table.rows,
      table.columns,
      "role",
      query({ status: "completed" }),
    );
    expect(result.base).toBe(2);
    expect(result.values).toEqual([
      { value: "ML Engineer", missing: false, count: 2 },
    ]);
  });
});

describe("facetableColumns", () => {
  it("offers every column, free prose included", () => {
    const keys = facetableColumns(table.columns).map((entry) => entry.key);
    expect(keys).toContain("file");
    expect(keys).toContain("role");
    expect(keys).toContain("skills");
    // A column the type guess read as prose is still the reader's to group by:
    // the guess is inferred from the values, and it is wrong often enough that
    // hiding the column left no way to ask for it.
    expect(keys).toContain("summary");
    expect(keys).toEqual(table.columns.map((column) => column.key));
  });
});

describe("coverageItems", () => {
  it("ranks the columns by how many documents have a value, thinnest first", () => {
    const items = coverageItems(table.rows, table.columns);
    expect(items[0]).toEqual({ label: "Experience", count: 2 });
    // Name and Added via are excluded: every document has both by definition.
    expect(items.map((entry) => entry.label)).not.toContain("Name");
    expect(items.map((entry) => entry.label)).not.toContain("Status");
  });
});

describe("histogram", () => {
  it("summarises a numeric column and skips the documents with no number", () => {
    const column = table.columns.find((entry) => entry.key === "experience")!;
    const result = histogram(table.rows, column)!;
    expect(result.count).toBe(2);
    expect(result.min).toBe(5);
    expect(result.max).toBe(7);
    expect(result.mean).toBe(6);
    expect(result.median).toBe(6);
    expect(result.sum).toBe(12);
    expect(
      result.buckets.reduce((total, bucket) => total + bucket.count, 0),
    ).toBe(2);
  });

  it("returns nothing for a column that holds no numbers", () => {
    const column = table.columns.find((entry) => entry.key === "role")!;
    expect(histogram(table.rows, column)).toBeNull();
  });
});
