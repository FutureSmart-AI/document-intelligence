import { isAxiosError } from "axios";

import {
  getDataViewTabular,
  getDataViewTabularByObjectIds,
  getDataViewTabularCursor,
  getKnowledgeDocuments,
  type KnowledgeDocumentSummary,
} from "@/platform/services/data-view";
import type {
  ColumnSchema,
  DataViewByObjectIdsRequest,
  DataViewCursorRequest,
  DataViewCursorResponse,
  DataViewRequest,
  DataViewResponse,
  DocumentExtractionContext,
} from "@/platform/types/data-view";
import type { CsvColumn } from "@/platform/utils/csv";

import { isColumnVisible } from "./columnVisibility";

/** Largest page the backend accepts on either route (422 above it). */
export const EXPORT_PAGE_SIZE = 100;

/**
 * Documented ceiling for a client-side export (see docs/api-flows/data-view.md).
 * Beyond this the browser export is refused and the user is asked to narrow
 * filters — a server-side streaming/S3 export is the backend follow-up.
 */
export const MAX_EXPORT_ROWS = 10_000;

type FetchCursorPage = (
  knowledgeId: string,
  request: DataViewCursorRequest,
) => Promise<DataViewCursorResponse>;

type FetchPage = (
  knowledgeId: string,
  request: DataViewRequest,
) => Promise<DataViewResponse>;

export interface FetchAllDataViewRowsArgs {
  knowledgeId: string;
  /** Same request the table is showing; pagination is replaced. */
  request: DataViewRequest;
  pageSize?: number;
  maxRows?: number;
  onProgress?: (fetched: number, total: number) => void;
  /** Abort further page requests (component unmount / user cancel). */
  signal?: AbortSignal;
  /** Injectable for tests. */
  fetchCursorPage?: FetchCursorPage;
  /** Injectable for tests. */
  fetchPage?: FetchPage;
}

export interface DataViewExportResult {
  extractionContext?: Record<string, DocumentExtractionContext>;
  rows: Record<string, unknown>[];
  columns: ColumnSchema[];
  /** `total_count` as reported by the API for the same filters. */
  totalCount: number;
  /** True when fewer rows were readable than the API's own total_count. */
  partial: boolean;
}

export class ExportTooLargeError extends Error {
  constructor(
    readonly totalCount: number,
    readonly maxRows: number,
  ) {
    super(
      `This result has ${totalCount.toLocaleString()} rows, more than the ` +
        `${maxRows.toLocaleString()} row export limit. Narrow the filters and try again.`,
    );
    this.name = "ExportTooLargeError";
  }
}

/** The v1 cursor route is API-key gated on some deployments. */
const isRouteUnavailable = (error: unknown): boolean =>
  isAxiosError(error) &&
  [401, 403, 404, 405].includes(error.response?.status ?? 0);

/**
 * Identity of a page, used only to notice a cursor that hands back the page it
 * just returned. Rows are NOT deduped by `object_id`: a knowledge base can hold
 * several metadata points for the same document (a 764-row base on production
 * has 667 distinct object_ids), and the table counts points, so dropping them
 * would make the CSV shorter than the count on screen.
 */
const pageIdentity = (rows: Record<string, unknown>[]): string =>
  rows.map((row, i) => String(row?.object_id ?? `#${i}`)).join("|");

/**
 * Reads every row matching the current filters/sort/columns.
 *
 * Walks the v1 cursor route (`limit` + `page_offset`), because v2 page numbers
 * still cannot be walked. They used to be ignored outright — every page came
 * back as page 1, which shipped "Exported 200 rows" for a 163-document base and
 * 800 for 764. Since the backend fix (verified 2026-08-11) `page` does advance,
 * but consecutive pages overlap and the walk never reaches part of the set: a
 * 779-point base returns 779 rows over 495 distinct `object_id`s, all of them a
 * subset of what one v1 cursor walk returns. Column parity with the table is
 * recovered separately by `enrichRowsWithFullSchema`.
 *
 * If the cursor route is unavailable (401/403/404), falls back to a single v2
 * request of `EXPORT_PAGE_SIZE` rows and reports the result as `partial` so the
 * UI can say how much of the set the file actually holds — never a count the
 * file does not back up.
 */
export const fetchAllDataViewRows = async ({
  knowledgeId,
  request,
  pageSize = EXPORT_PAGE_SIZE,
  maxRows = MAX_EXPORT_ROWS,
  onProgress,
  signal,
  fetchCursorPage = getDataViewTabularCursor,
  fetchPage = getDataViewTabular,
}: FetchAllDataViewRowsArgs): Promise<DataViewExportResult> => {
  const { page: _page, page_size: _pageSize, ...filterRequest } = request;
  void _page;
  void _pageSize;

  onProgress?.(0, 0);

  const rows: Record<string, unknown>[] = [];
  const extractionContext: Record<string, DocumentExtractionContext> = {};
  let columns: ColumnSchema[] = [];
  let totalCount = 0;
  let offset: string | null | undefined;
  let previousPageIdentity: string | null = null;
  let firstResponse = true;

  while (true) {
    if (signal?.aborted) break;

    let page: DataViewCursorResponse;
    try {
      page = await fetchCursorPage(knowledgeId, {
        ...filterRequest,
        limit: pageSize,
        page_offset: offset ?? null,
      });
    } catch (error) {
      if (firstResponse && isRouteUnavailable(error)) {
        return fetchSingleV2Page({
          knowledgeId,
          request: filterRequest,
          pageSize,
          maxRows,
          onProgress,
          signal,
          fetchPage,
        });
      }
      throw error;
    }

    if (firstResponse) {
      columns = page.columns ?? [];
      totalCount = page.total_count ?? 0;
      if (totalCount > maxRows) {
        throw new ExportTooLargeError(totalCount, maxRows);
      }
      firstResponse = false;
    }

    const pageRows = page.rows ?? [];
    Object.assign(extractionContext, page.extraction_context ?? {});
    const identity = pageIdentity(pageRows);
    // A cursor that hands back the page it just gave would loop forever.
    if (pageRows.length > 0 && identity === previousPageIdentity) break;
    previousPageIdentity = identity;

    rows.push(...pageRows);
    onProgress?.(rows.length, Math.max(totalCount, rows.length));

    if (rows.length >= maxRows) break;
    if (!pageRows.length) break;
    if (!page.has_more || !page.next_page_offset) break;
    offset = page.next_page_offset;
  }

  const capped = rows.slice(0, maxRows);
  return {
    rows: capped,
    ...(Object.keys(extractionContext).length ? { extractionContext } : {}),
    columns,
    totalCount: Math.max(totalCount, capped.length),
    partial: capped.length < totalCount,
  };
};

/**
 * Fallback when the cursor route is not reachable: one v2 request. Cannot return
 * more than `EXPORT_PAGE_SIZE` rows (v2 pages overlap, so walking them would
 * drop documents), so anything larger comes back flagged `partial`.
 */
const fetchSingleV2Page = async ({
  knowledgeId,
  request,
  pageSize,
  maxRows,
  onProgress,
  signal,
  fetchPage,
}: {
  knowledgeId: string;
  request: DataViewRequest;
  pageSize: number;
  maxRows: number;
  onProgress?: (fetched: number, total: number) => void;
  signal?: AbortSignal;
  fetchPage: FetchPage;
}): Promise<DataViewExportResult> => {
  const response = await fetchPage(knowledgeId, {
    ...request,
    page: 1,
    page_size: pageSize,
  });

  if (signal?.aborted) {
    return { rows: [], columns: [], totalCount: 0, partial: false };
  }

  const rows = response.rows ?? [];
  const totalCount = response.total_count ?? rows.length;

  if (Math.max(totalCount, rows.length) > maxRows) {
    throw new ExportTooLargeError(Math.max(totalCount, rows.length), maxRows);
  }

  onProgress?.(rows.length, totalCount);

  return {
    rows,
    columns: response.columns ?? [],
    ...(response.extraction_context
      ? { extractionContext: response.extraction_context }
      : {}),
    totalCount,
    partial: rows.length < totalCount,
  };
};

/** Documents per `object_id in [...]` request during column enrichment. */
export const ENRICH_BATCH_SIZE = 50;

type FetchByObjectIds = (
  knowledgeId: string,
  request: DataViewByObjectIdsRequest,
) => Promise<DataViewResponse>;

export interface EnrichRowsWithFullSchemaArgs {
  knowledgeId: string;
  /** Rows from the v1 cursor walk, in export order. */
  rows: Record<string, unknown>[];
  /** Schema the v1 walk returned. */
  columns: ColumnSchema[];
  /** The table's filters/sort; ANDed with the id filter. */
  request?: DataViewRequest;
  batchSize?: number;
  pageSize?: number;
  onProgress?: (done: number, total: number) => void;
  signal?: AbortSignal;
  /** Injectable for tests. */
  fetchByObjectIds?: FetchByObjectIds;
}

interface RowGroup {
  /** null for rows the export cannot look up again. */
  objectId: string | null;
  rows: Record<string, unknown>[];
}

/** Rows grouped by `object_id`, in first-seen order; id-less rows stay alone. */
const groupByObjectId = (rows: Record<string, unknown>[]): RowGroup[] => {
  const groups: RowGroup[] = [];
  const byId = new Map<string, RowGroup>();

  rows.forEach((row) => {
    const id = row?.object_id ? String(row.object_id) : null;
    if (!id) {
      groups.push({ objectId: null, rows: [row] });
      return;
    }
    const existing = byId.get(id);
    if (existing) {
      existing.rows.push(row);
      return;
    }
    const group: RowGroup = { objectId: id, rows: [row] };
    byId.set(id, group);
    groups.push(group);
  });

  return groups;
};

/**
 * Re-reads the exported documents through the **v2** route to get the columns
 * the v1 export route projects away.
 *
 * v2 owns the wide schema the table renders (connector fields: `file_name`,
 * `source`, `last_modified`, every `clickup_*`) — a ClickUp base returns 22
 * columns on v2 against 10 on v1 — but its `page` pagination hands back
 * overlapping windows, so walking it loses documents (verified 2026-08-11:
 * a 779-point base yields 779 rows over only 495 distinct `object_id`s, a
 * strict subset of what the v1 cursor returns). Filtering by `object_id`
 * avoids pagination altogether: each batch is a single page.
 *
 * Batches whose rows would not fit one page are split and retried, because a
 * document can own several metadata points. Any failure leaves the v1 rows
 * untouched — a narrower CSV beats no CSV.
 */
export const enrichRowsWithFullSchema = async ({
  knowledgeId,
  rows,
  columns,
  request,
  batchSize = ENRICH_BATCH_SIZE,
  pageSize = EXPORT_PAGE_SIZE,
  onProgress,
  signal,
  fetchByObjectIds = getDataViewTabularByObjectIds,
}: EnrichRowsWithFullSchemaArgs): Promise<{
  rows: Record<string, unknown>[];
  columns: ColumnSchema[];
}> => {
  const groups = groupByObjectId(rows);
  const objectIds = groups
    .map((group) => group.objectId)
    .filter((id): id is string => Boolean(id));
  if (!objectIds.length) return { rows, columns };

  const enriched = new Map<string, Record<string, unknown>[]>();
  let wideColumns: ColumnSchema[] = [];
  let done = 0;

  const readBatch = async (batch: string[]): Promise<void> => {
    const response = await fetchByObjectIds(knowledgeId, {
      objectIds: batch,
      page_size: pageSize,
      columns: request?.columns,
      filters: request?.filters,
      sort: request?.sort,
    });

    const batchRows = response.rows ?? [];
    const matched = response.total_count ?? batchRows.length;

    // More matching points than one page holds: halve the batch rather than
    // silently keep the truncated page.
    if (matched > batchRows.length && batch.length > 1) {
      const middle = Math.ceil(batch.length / 2);
      await readBatch(batch.slice(0, middle));
      await readBatch(batch.slice(middle));
      return;
    }

    if (!wideColumns.length) wideColumns = response.columns ?? [];
    batchRows.forEach((row) => {
      const id = String(row?.object_id ?? "");
      if (!id) return;
      const bucket = enriched.get(id);
      if (bucket) bucket.push(row);
      else enriched.set(id, [row]);
    });
  };

  try {
    for (let i = 0; i < objectIds.length; i += batchSize) {
      if (signal?.aborted) return { rows, columns };
      const batch = objectIds.slice(i, i + batchSize);
      await readBatch(batch);
      done += batch.length;
      onProgress?.(done, objectIds.length);
    }
  } catch {
    return { rows, columns };
  }

  if (!enriched.size) return { rows, columns };

  const merged = groups.flatMap(
    (group) =>
      (group.objectId ? enriched.get(group.objectId) : null) ?? group.rows,
  );

  const present = new Set(wideColumns.map((col) => col.name));
  return {
    rows: merged,
    columns: [
      ...wideColumns,
      ...columns.filter((col) => col?.name && !present.has(col.name)),
    ],
  };
};

/**
 * Document-level fields the v2 table renders but the v1 cursor route leaves out
 * of both its schema and its rows. Verified on production: v2 returns 37 columns
 * for the resume base, v1 returns the same 36 minus `file_name`.
 */
const DOCUMENT_BACKFILL_FIELDS = [
  "file_name",
  "url",
  "status",
  "created_time",
  "updated_time",
] as const;

export interface BackfillDocumentColumnsArgs {
  knowledgeId: string;
  rows: Record<string, unknown>[];
  columns: ColumnSchema[];
  /** Column names on screen; only these are worth filling in. */
  visibleColumns?: string[];
  /** Injectable for tests. */
  fetchDocuments?: (knowledgeId: string) => Promise<KnowledgeDocumentSummary[]>;
}

/**
 * Adds the document-level columns the table shows but the export route did not
 * return, joined on `object_id`. Failures are swallowed: a missing filename
 * column must not cost the user the export they asked for.
 */
export const backfillDocumentColumns = async ({
  knowledgeId,
  rows,
  columns,
  visibleColumns,
  fetchDocuments = getKnowledgeDocuments,
}: BackfillDocumentColumnsArgs): Promise<{
  rows: Record<string, unknown>[];
  columns: ColumnSchema[];
}> => {
  const present = new Set(columns.map((col) => col.name));
  const missing = (visibleColumns ?? []).filter(
    (name) =>
      !present.has(name) &&
      (DOCUMENT_BACKFILL_FIELDS as readonly string[]).includes(name),
  );

  if (!missing.length || !rows.length) return { rows, columns };

  let documents: KnowledgeDocumentSummary[];
  try {
    documents = await fetchDocuments(knowledgeId);
  } catch {
    return { rows, columns };
  }

  const byObjectId = new Map(
    documents
      .filter((doc) => doc?.object_id)
      .map((doc) => [String(doc.object_id), doc] as const),
  );
  if (!byObjectId.size) return { rows, columns };

  const filled = rows.map((row) => {
    const doc = byObjectId.get(String(row.object_id ?? ""));
    const extra: Record<string, unknown> = {};
    missing.forEach((name) => {
      extra[name] = doc?.[name as keyof KnowledgeDocumentSummary] ?? "";
    });
    return { ...row, ...extra };
  });

  return {
    rows: filled,
    columns: [
      ...columns,
      ...missing.map((name) => ({ name, original_field: name })),
    ],
  };
};

const humanizeColumnName = (name: string): string =>
  name
    .split("_")
    .map((word) => (word ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(" ");

/**
 * CSV columns for the export, in the order the API returned them, using the
 * same humanized headers the table renders.
 *
 * The file carries the columns that are on screen, not the whole metadata
 * schema: pass `visibleColumns` (the table's visible column names) to pin them.
 * Without it, visibility is derived from the export's own schema. Either way, if
 * that leaves nothing — a schema the table and the export route disagree on —
 * every column is written rather than a header-less file.
 */
export const toCsvColumns = (
  columns: ColumnSchema[],
  visibleColumns?: string[],
): CsvColumn[] => {
  const seen = new Set<string>();
  const unique = columns.filter((col) => {
    if (!col?.name || seen.has(col.name)) return false;
    seen.add(col.name);
    return true;
  });

  const allowed = visibleColumns?.length ? new Set(visibleColumns) : null;
  const shown = unique.filter((col) =>
    allowed ? allowed.has(col.name) : isColumnVisible(col),
  );

  return (shown.length ? shown : unique).map((col) => ({
    key: col.name,
    label: humanizeColumnName(col.name),
  }));
};
