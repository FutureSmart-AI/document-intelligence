import {
  csvFilename,
  downloadRowsAsCsv,
  type CsvColumn,
} from "@/platform/utils/csv";

import { cellText } from "./format";
import type { DocumentRow, TableColumn } from "./types";

function contextColumnKey(columns: TableColumn[]): string {
  const keys = new Set(columns.map((column) => column.key));
  let key = "__document_extraction_context";
  while (keys.has(key)) key = `_${key}`;
  return key;
}

/**
 * The two exports the toolbar offers.
 *
 * Both are built from rows the screen already holds, so neither re-reads the
 * service and neither can disagree with what is on screen: "what I am looking
 * at" takes the visible columns and the filtered, sorted rows; "everything"
 * takes every column and every document. The object id rides along in both,
 * because a CSV of extracted values with no key back to the document is not
 * something you can join to anything.
 */
export function tableToCsvRows(
  rows: DocumentRow[],
  columns: TableColumn[],
): Record<string, unknown>[] {
  return rows.map((row) => {
    const record: Record<string, unknown> = { object_id: row.objectId };
    for (const column of columns) {
      record[column.key] = cellText(column, row.values[column.key]);
    }
    record[contextColumnKey(columns)] = JSON.stringify(
      row.extractionContext ?? {
        status: "unknown",
        event_key: null,
        processed_at: null,
        processed_fields: null,
        definition_history: "unknown",
      },
    );
    return record;
  });
}

export function tableCsvColumns(columns: TableColumn[]): CsvColumn[] {
  return [
    { key: "object_id", label: "Object ID" },
    ...columns.map((column) => ({ key: column.key, label: column.label })),
    { key: contextColumnKey(columns), label: "Extraction context (JSON)" },
  ];
}

export function exportTableCsv({
  rows,
  columns,
  prefix,
}: {
  rows: DocumentRow[];
  columns: TableColumn[];
  /** `documents-visible` or `documents-all`, so the two files never collide. */
  prefix: string;
}): void {
  downloadRowsAsCsv(
    tableToCsvRows(rows, columns),
    csvFilename(prefix),
    tableCsvColumns(columns),
  );
}
