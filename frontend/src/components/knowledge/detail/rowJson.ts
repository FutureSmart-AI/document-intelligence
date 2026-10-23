import type { DocumentRow } from "@/components/knowledge/table/types";

/**
 * One document's row as JSON — what the header's JSON button copies and what
 * the panel's JSON view shows, from this one place so the two never disagree.
 *
 * What this document was processed under, like the field list: a field added
 * since would come out as `null`, which reads as "looked for, not found".
 */
export function rowObject(row: DocumentRow): Record<string, unknown> {
  const values = Object.fromEntries(
    Object.entries(row.values).filter(([key]) => !row.notExtracted.has(key)),
  );
  return { object_id: row.objectId, ...values };
}

export function rowJson(row: DocumentRow): string {
  return JSON.stringify(rowObject(row), null, 2);
}
