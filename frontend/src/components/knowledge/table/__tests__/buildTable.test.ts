import { describe, expect, it } from "vitest";

import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";
import type { ColumnSchema } from "@/platform/types/data-view";

import {
  addedViaLabel,
  buildTable,
  deriveValueType,
  flattenDocuments,
} from "../buildTable";

/**
 * The join is the whole screen.
 *
 * Two routes own one row — the documents list has the name, the status and the
 * timestamps, `/documents/metadata/all` has the extracted fields — and every
 * control on the screen reads the joined result. So the cases worth pinning are
 * the ones where the two disagree: a document with no metadata row yet, a
 * metadata row for a document that is gone, a crawled sub-page, and a
 * connector column that is not an extracted field at all.
 */

const document = (over: Partial<KnowledgeItem> = {}): KnowledgeItem =>
  ({
    id: "1",
    object_id: "obj-1",
    file_name: "Ananya_Rao_Resume.pdf",
    status: "completed",
    url: null,
    created_time: "2026-07-28T10:00:00Z",
    updated_time: "2026-07-29T10:00:00Z",
    content_type: "file",
    s3_url: "",
    parent_url: null,
    ...over,
  }) as KnowledgeItem;

const column = (
  name: string,
  over: Partial<ColumnSchema> = {},
): ColumnSchema => ({
  name,
  original_field: name,
  data_type: "str",
  ...over,
});

describe("buildTable", () => {
  it("joins a document to its metadata row and counts the fields that landed", () => {
    const table = buildTable({
      documents: [document()],
      metadataColumns: [
        column("candidate"),
        column("skills", { data_type: "List[str]" }),
        column("availability"),
        // A connector key, not something the extraction found.
        column("file_name"),
      ],
      metadataRows: [
        {
          object_id: "obj-1",
          candidate: "Ananya Rao",
          skills: ["Python", "Kubernetes"],
          availability: "",
          file_name: "Ananya_Rao_Resume.pdf",
        },
      ],
    });

    expect(table.fieldColumns.map((entry) => entry.key)).toEqual([
      "candidate",
      "skills",
      "availability",
    ]);
    expect(table.rows).toHaveLength(1);
    expect(table.rows[0].values.file).toBe("Ananya_Rao_Resume.pdf");
    expect(table.rows[0].values.addedvia).toBe("File");
    expect(table.rows[0].values.status).toBe("Completed");
    expect(table.rows[0].values.candidate).toBe("Ananya Rao");
    // Two of three: an empty string is not a value the extraction found.
    expect(table.rows[0].filled).toBe(2);
    expect(table.rows[0].values.filled).toBe(2);
  });

  /**
   * The metadata route pads every row to today's schema with `null`, so a field
   * added after a document was read looked like a failed extraction. The
   * enrichment log's field list is the schema the document ran under.
   */
  it("marks empty fields the document was never processed against", () => {
    const table = buildTable({
      documents: [document(), document({ object_id: "obj-2" })],
      metadataColumns: [
        column("invoice_number"),
        column("line_items_0_amount", { original_field: "line_items" }),
        column("distance_miles", { data_type: "int" }),
        column("currency"),
      ],
      metadataRows: [
        {
          object_id: "obj-1",
          invoice_number: "INV-4402",
          line_items_0_amount: 680,
          distance_miles: null,
          // Re-added under the same name: the old value is still real.
          currency: "GBP",
        },
        { object_id: "obj-2", invoice_number: null, distance_miles: null },
      ],
      extractedFields: new Map([
        ["obj-1", new Set(["invoice_number", "line_items"])],
      ]),
    });

    const [processed, unlogged] = table.rows;
    expect([...processed.notExtracted]).toEqual(["distance_miles"]);
    expect(processed.asked).toBe(3);
    expect(processed.filled).toBe(3);
    // No log entry: measured against today's schema, as before.
    expect(unlogged.notExtracted.size).toBe(0);
    expect(unlogged.asked).toBe(4);
  });

  it("still gives a document a row when no metadata has arrived for it", () => {
    const table = buildTable({
      documents: [document({ status: "failed" })],
      metadataColumns: [column("candidate")],
      metadataRows: [],
    });

    expect(table.rows).toHaveLength(1);
    expect(table.rows[0].values.status).toBe("Failed");
    expect(table.rows[0].filled).toBe(0);
  });

  it("ignores metadata for documents the list no longer has", () => {
    const table = buildTable({
      documents: [document()],
      metadataColumns: [column("candidate")],
      metadataRows: [
        { object_id: "obj-1", candidate: "Ananya Rao" },
        { object_id: "obj-gone", candidate: "Deleted Person" },
      ],
    });

    expect(table.rows.map((row) => row.objectId)).toEqual(["obj-1"]);
  });

  it("keeps the first metadata row when a document owns several", () => {
    const table = buildTable({
      documents: [document()],
      metadataColumns: [column("candidate")],
      metadataRows: [
        { object_id: "obj-1", candidate: "Ananya Rao" },
        { object_id: "obj-1", candidate: "A second metadata point" },
      ],
    });

    expect(table.rows[0].values.candidate).toBe("Ananya Rao");
  });

  it("puts the derived status columns in their own group", () => {
    const table = buildTable({
      documents: [document()],
      metadataColumns: [column("candidate")],
      metadataRows: [],
    });

    const byGroup = (group: string) =>
      table.columns
        .filter((entry) => entry.group === group)
        .map((entry) => entry.key);

    expect(byGroup("doc")).toEqual(["file", "addedvia"]);
    expect(byGroup("status")).toEqual([
      "status",
      "created",
      "updated",
      "filled",
    ]);
    expect(byGroup("fields")).toEqual(["candidate"]);
  });
});

describe("flattenDocuments", () => {
  it("turns each crawled sub-page into a document of its own", () => {
    const flat = flattenDocuments([
      document({
        object_id: "seed",
        content_type: "url",
        url: "https://example.com",
        sub_urls: [
          {
            id: "2",
            object_id: "page-1",
            file_name: null,
            status: "processing",
            url: "https://example.com/a",
            source_url: "https://example.com",
            created_time: "2026-07-28T10:00:00Z",
            content_type: "url",
          },
        ],
      }),
    ]);

    expect(flat.map((entry) => entry.object_id)).toEqual(["seed", "page-1"]);
    // With no name of its own, a crawled page is identified by its URL.
    expect(flat[1].file_name).toBe("https://example.com/a");
    expect(flat[1].status).toBe("processing");
    expect(flat[1].sub_urls).toBeUndefined();
  });

  it("drops a repeated object id rather than showing it twice", () => {
    const flat = flattenDocuments([document(), document()]);
    expect(flat).toHaveLength(1);
  });
});

describe("addedViaLabel", () => {
  it("prefers the source the service reports over the content type", () => {
    expect(
      addedViaLabel(document({ content_type: "file", source: "clickup" })),
    ).toBe("ClickUp");
    expect(addedViaLabel(document({ content_type: "url" }))).toBe("Website");
    expect(addedViaLabel(document({ content_type: "plain_text" }))).toBe(
      "Text",
    );
  });
});

describe("deriveValueType", () => {
  it("reads numbers, lists and dates from the schema", () => {
    expect(deriveValueType(column("pages", { data_type: "int" }), [])).toBe(
      "num",
    );
    expect(
      deriveValueType(column("skills", { data_type: "List[str]" }), []),
    ).toBe("list");
    expect(deriveValueType(column("joined_at"), ["2026-01-01T00:00:00Z"])).toBe(
      "date",
    );
  });

  it("separates a reused label from free prose", () => {
    const roles = ["ML Engineer", "ML Engineer", "Frontend Engineer"];
    expect(deriveValueType(column("role"), roles)).toBe("cat");

    const summaries = Array.from(
      { length: 6 },
      (_, index) =>
        `A long, unique summary of this document number ${index} that nobody would want to group by`,
    );
    expect(deriveValueType(column("summary"), summaries)).toBe("text");
  });
});
