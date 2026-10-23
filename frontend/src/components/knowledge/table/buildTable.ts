import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";
import type {
  ColumnSchema,
  DocumentExtractionContext,
} from "@/platform/types/data-view";

import {
  ADDED_VIA_LABELS,
  DOC_COLUMNS,
  DOCUMENT_COLUMN_KEYS,
  NON_FIELD_COLUMNS,
  STATUS_COLUMNS,
  STATUS_LABELS,
  statusBucket,
} from "./constants";
import { isEmptyValue, toColumnLabel } from "./format";
import type { DocumentRow, TableColumn, ValueType } from "./types";

export interface KnowledgeTable {
  /** Document columns first, then status details, then the extracted fields. */
  columns: TableColumn[];
  rows: DocumentRow[];
  /** The extracted-field columns, which is what "Fields filled" counts against. */
  fieldColumns: TableColumn[];
}

type SubUrl = NonNullable<KnowledgeItem["sub_urls"]>[number];

/** How the Added via column labels a document. */
export function addedViaLabel(document: KnowledgeItem): string {
  const source = (document.source ?? document.content_type ?? "").toLowerCase();
  return ADDED_VIA_LABELS[source] ?? "File";
}

/**
 * A crawled sub-page as a document of its own.
 *
 * A website crawl produces one document per page, each with its own status and
 * its own extracted fields, and the metadata route returns them as rows like
 * any other. The old list nested them under the seed URL; this table is flat,
 * so they are rows — otherwise the grid would show 1 document where the
 * collection holds 40 and the coverage view would measure the wrong set.
 */
function subUrlAsDocument(parent: KnowledgeItem, sub: SubUrl): KnowledgeItem {
  return {
    ...parent,
    id: sub.id,
    object_id: sub.object_id,
    file_name: sub.file_name || sub.url || "Untitled page",
    status: sub.status,
    url: sub.url,
    source_url: sub.source_url,
    created_time: sub.created_time,
    updated_time: sub.updated_at ?? null,
    content_type: (sub.content_type as KnowledgeItem["content_type"]) ?? "url",
    s3_url: sub.s3_url ?? "",
    sub_urls: undefined,
    parent_url: parent.url ?? null,
  };
}

/** Every document in the collection, seed pages and crawled pages alike. */
export function flattenDocuments(
  documents: KnowledgeItem[] | undefined,
): KnowledgeItem[] {
  const flat: KnowledgeItem[] = [];
  const seen = new Set<string>();
  for (const document of documents ?? []) {
    if (!document?.object_id || seen.has(document.object_id)) continue;
    seen.add(document.object_id);
    flat.push(document);
    for (const sub of document.sub_urls ?? []) {
      if (!sub?.object_id || seen.has(sub.object_id)) continue;
      seen.add(sub.object_id);
      flat.push(subUrlAsDocument(document, sub));
    }
  }
  return flat;
}

/** A string column whose values look like timestamps, by name or by content. */
function looksLikeDate(name: string, values: unknown[]): boolean {
  if (/(^|_)(date|time|at|on)$/.test(name)) return true;
  const sample = values
    .filter((value) => typeof value === "string")
    .slice(0, 20);
  if (!sample.length) return false;
  return sample.every((value) =>
    /^\d{4}-\d{2}-\d{2}([T ]|$)/.test(value as string),
  );
}

/**
 * The display type of one column.
 *
 * The service's own type only distinguishes `str`, `int`, `float`, `bool` and
 * `List[str]`, so "is this a date", "is this a small vocabulary worth grouping
 * by" and "is this free prose" have to be answered from the values that are
 * actually there. Getting it wrong is cheap and visible — a column lands in the
 * wrong viz mode — and guessing per *cell* rather than per column is what makes
 * a grid render the same field five different ways.
 */
export function deriveValueType(
  column: ColumnSchema,
  values: unknown[],
): ValueType {
  const dataType = column.data_type;
  if (dataType === "int" || dataType === "float") return "num";
  if (dataType === "List[str]" || column.parent_type === "list_str")
    return "list";
  if (dataType === "bool") return "cat";
  if (dataType === "dict" || dataType === "List[dict]") return "text";

  const present = values.filter((value) => !isEmptyValue(value));
  if (looksLikeDate(column.name, present)) return "date";
  if (!present.length) return "cat";

  const texts = present.map((value) => String(value));
  const distinct = new Set(texts);
  const longest = texts.reduce((max, text) => Math.max(max, text.length), 0);
  // A category is a value the collection *reuses*: a short label that recurs.
  // A summary or a headline is unique per document and long, and grouping by it
  // would produce one bucket per row.
  const reused = distinct.size <= Math.max(12, Math.ceil(texts.length / 2));
  return reused && longest <= 48 ? "cat" : "text";
}

/**
 * Joins the documents list to the flattened metadata table.
 *
 * Two routes, one row: the documents list owns the name, how the document
 * arrived, its status and its timestamps; `/documents/metadata/all` owns every
 * extracted field. A document with no metadata row yet — one still being read,
 * or one that failed — still gets a row, with its fields empty, because "we
 * could not read this file" is exactly what the reader is here to see.
 */
export function buildTable({
  documents,
  metadataColumns,
  metadataRows,
  extractedFields,
  extractionContext,
}: {
  documents: KnowledgeItem[] | undefined;
  metadataColumns: ColumnSchema[] | undefined;
  metadataRows: Record<string, unknown>[] | undefined;
  /**
   * The fields each document was processed against, from the enrichment log.
   * A document missing from it is measured against today's schema, as before.
   */
  extractedFields?: ReadonlyMap<string, ReadonlySet<string>>;
  extractionContext?: Record<string, DocumentExtractionContext>;
}): KnowledgeTable {
  const flat = flattenDocuments(documents);

  // A document can own several metadata points; the first row carries the
  // document-level extraction, which is what these columns show.
  const byObjectId = new Map<string, Record<string, unknown>>();
  for (const row of metadataRows ?? []) {
    const objectId = row?.object_id;
    if (typeof objectId !== "string" || byObjectId.has(objectId)) continue;
    byObjectId.set(objectId, row);
  }

  const schema = (metadataColumns ?? []).filter(
    (column) =>
      !!column?.name &&
      !NON_FIELD_COLUMNS.has(column.name) &&
      !DOCUMENT_COLUMN_KEYS.has(column.name),
  );

  const fieldColumns: TableColumn[] = schema.map((column) => {
    const values = (metadataRows ?? []).map((row) => row?.[column.name]);
    return {
      key: column.name,
      label: toColumnLabel(column.name),
      group: "fields" as const,
      vtype: deriveValueType(column, values),
      type: column.data_type ?? column.parent_type ?? "str",
      field: column.original_field || column.name,
    };
  });

  const rows: DocumentRow[] = flat.map((document) => {
    const metadata = byObjectId.get(document.object_id) ?? {};
    const values: Record<string, unknown> = {
      file: document.file_name || document.url || "Untitled",
      addedvia: addedViaLabel(document),
      // The bucket, not the raw status: the service reports a dozen in-flight
      // states and the column has to group by three. The cell keeps the raw
      // value in its tooltip.
      status: STATUS_LABELS[statusBucket(document.status)],
      created: document.created_time ?? "",
      updated: document.updated_time ?? document.created_time ?? "",
    };
    const processedUnder = extractedFields?.get(document.object_id);
    const notExtracted = new Set<string>();
    let filled = 0;
    for (const column of fieldColumns) {
      const value = metadata[column.key];
      values[column.key] = value;
      if (!isEmptyValue(value)) filled += 1;
      // Only an empty cell can be "never extracted": a field deleted and added
      // back under the same name keeps the values it already had, and those
      // are real whatever the latest attempt was asked for.
      else if (
        processedUnder &&
        !processedUnder.has(column.field ?? column.key)
      )
        notExtracted.add(column.key);
    }
    values.filled = filled;
    return {
      objectId: document.object_id,
      extractionContext: extractionContext?.[document.object_id],
      document,
      values,
      filled,
      notExtracted,
      asked: fieldColumns.length - notExtracted.size,
    };
  });

  return {
    columns: [...DOC_COLUMNS, ...STATUS_COLUMNS, ...fieldColumns],
    rows,
    fieldColumns,
  };
}
