import { describe, expect, it } from "vitest";

import { rowsToCsv } from "@/platform/utils/csv";

import { tableCsvColumns, tableToCsvRows } from "../exportCsv";
import type { DocumentRow, TableColumn } from "../types";

/**
 * The CSV is what the reader is looking at, plus the key back to the document.
 *
 * Worth pinning because the two menu items differ only in what they are handed:
 * "what I am looking at" gets the filtered rows and the visible columns,
 * "everything" gets all of both. Both go through this one serialiser, so a
 * column shown as a list in the grid has to come out as one cell either way,
 * and the object id has to be there or the file joins to nothing.
 */

const columns: TableColumn[] = [
  { key: "file", label: "Name", group: "doc", vtype: "text", type: "str" },
  {
    key: "skills",
    label: "Skills",
    group: "fields",
    vtype: "list",
    type: "List[str]",
  },
  {
    key: "created",
    label: "Created",
    group: "status",
    vtype: "date",
    type: "date",
  },
];

const rows = [
  {
    objectId: "obj-1",
    document: {} as DocumentRow["document"],
    filled: 1,
    notExtracted: new Set<string>(),
    asked: 2,
    values: {
      file: "Ananya_Rao_Resume.pdf",
      skills: ["Python", "Kubernetes"],
      created: "2026-07-28T10:00:00Z",
    },
  },
] satisfies DocumentRow[];

const unknownContext = JSON.stringify({
  status: "unknown",
  event_key: null,
  processed_at: null,
  processed_fields: null,
  definition_history: "unknown",
});

describe("tableToCsvRows", () => {
  it("carries the object id and flattens each cell to its displayed text", () => {
    expect(tableToCsvRows(rows, columns)).toEqual([
      {
        object_id: "obj-1",
        file: "Ananya_Rao_Resume.pdf",
        skills: "Python, Kubernetes",
        created: "Jul 28, 2026",
        __document_extraction_context: unknownContext,
      },
    ]);
  });

  it("labels the header with the column names the grid shows", () => {
    expect(tableCsvColumns(columns).map((column) => column.label)).toEqual([
      "Object ID",
      "Name",
      "Skills",
      "Created",
      "Extraction context (JSON)",
    ]);
  });

  it("serialises to a CSV whose header matches its one row", () => {
    const csv = rowsToCsv(
      tableToCsvRows(rows, columns),
      tableCsvColumns(columns),
    );
    const [header, body] = csv.split("\r\n");
    expect(header).toBe(
      "Object ID,Name,Skills,Created,Extraction context (JSON)",
    );
    // The comma inside the joined skills list has to be quoted, not split.
    expect(body).toBe(
      'obj-1,Ananya_Rao_Resume.pdf,"Python, Kubernetes","Jul 28, 2026",' +
        '"' +
        unknownContext.replaceAll('"', '""') +
        '"',
    );
  });

  it("distinguishes attempted-empty from never-processed without replacing values", () => {
    const history = {
      status: "recorded" as const,
      event_key: "successful-A",
      processed_at: "2026-10-20T00:00:00Z",
      processed_fields: ["loyalty", "damage", "line_items"],
      definition_history: "unknown" as const,
    };
    const fieldColumns: TableColumn[] = [
      "loyalty",
      "damage",
      "total_due",
      "quantity",
      "nested",
    ].map((key) => ({
      key,
      label: key,
      group: "fields",
      vtype: "text",
      type: "str",
    }));
    const row: DocumentRow = {
      ...rows[0],
      extractionContext: history,
      values: {
        loyalty: null,
        damage: [],
        total_due: null,
        quantity: 0,
        nested: { quantity: 2 },
      },
    };
    const output = tableToCsvRows([row], fieldColumns)[0];
    expect(output.loyalty).toBe("");
    expect(output.damage).toBe("");
    expect(output.total_due).toBe("");
    expect(output.quantity).toBe("0");
    expect(output.nested).toBe('{"quantity":2}');
    const context = JSON.parse(String(output.__document_extraction_context));
    expect(context.processed_fields).toContain("loyalty");
    expect(context.processed_fields).toContain("damage");
    expect(context.processed_fields).not.toContain("total_due");
    expect(context.definition_history).toBe("unknown");
  });

  it("does not overwrite a same-named extracted column", () => {
    const collision: TableColumn = {
      key: "__document_extraction_context",
      label: "User field",
      group: "fields",
      vtype: "text",
      type: "str",
    };
    const output = tableToCsvRows(
      [{ ...rows[0], values: { __document_extraction_context: "original" } }],
      [collision],
    )[0];
    expect(output.__document_extraction_context).toBe("original");
    expect(output.___document_extraction_context).toBe(unknownContext);
    expect(tableCsvColumns([collision]).map((column) => column.key)).toEqual([
      "object_id",
      "__document_extraction_context",
      "___document_extraction_context",
    ]);
  });
});
