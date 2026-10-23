import type { ColumnSchema } from "@/platform/types/data-view";

/**
 * Columns the table falls back to when the API does not mark which columns the
 * user selected. Metadata schemas are wide (dozens of flattened columns), so the
 * table only opens with the ones that identify a document.
 */
export const DEFAULT_VISIBLE_COLUMNS = [
  "object_id",
  "title",
  "document_type",
  "summary",
  "keywords",
  "topics",
];

/** Single source of truth for "is this column on screen" — table and CSV share it. */
export const isColumnVisible = (col: ColumnSchema): boolean =>
  typeof col.is_user_selected === "boolean"
    ? col.is_user_selected
    : DEFAULT_VISIBLE_COLUMNS.includes(col.name);

export const visibleColumnNames = (columns: ColumnSchema[]): string[] =>
  columns.filter(isColumnVisible).map((col) => col.name);
