import { describe, expect, it } from "vitest";

import { extractedFieldsByDocument } from "../extractionLog";

/**
 * Which schema a document ran under is read off its enrichment attempts, so
 * the cases worth pinning are the ones with more than one attempt: a reprocess
 * under a newer schema, and a failed retry after a success.
 */

const fields = (...names: string[]) =>
  names.map((field_name) => ({ field_name }));

describe("extractedFieldsByDocument", () => {
  it("takes the most recent successful attempt's fields", () => {
    const map = extractedFieldsByDocument([
      {
        object_id: "a",
        created_at: "2026-09-18T19:21:43Z",
        status: "success",
        field_data: fields("summary", "invoice_reference"),
      },
      {
        object_id: "a",
        created_at: "2026-09-21T07:23:55Z",
        status: "success",
        field_data: fields("summary", "currency"),
      },
    ]);
    expect([...(map.get("a") ?? [])]).toEqual(["summary", "currency"]);
  });

  it("keeps the last success over a later failed retry", () => {
    const map = extractedFieldsByDocument([
      {
        object_id: "a",
        created_at: "2026-09-22T00:00:00Z",
        status: "error",
        field_data: fields("summary", "distance_miles"),
      },
      {
        object_id: "a",
        created_at: "2026-09-21T00:00:00Z",
        status: "success",
        field_data: fields("summary"),
      },
    ]);
    expect([...(map.get("a") ?? [])]).toEqual(["summary"]);
  });

  it("ignores attempts that carry no field list", () => {
    const map = extractedFieldsByDocument([
      { object_id: "a", status: "success", field_data: null },
      { object_id: "b", status: "success", field_data: [] },
      { object_id: null, status: "success", field_data: fields("summary") },
    ]);
    expect(map.size).toBe(0);
  });
});
