"use client";

import { useMemo } from "react";

import { useQuery } from "@tanstack/react-query";

import {
  MAX_TABLE_ROWS,
  METADATA_PAGE_SIZE,
} from "@/components/knowledge/table/constants";
import {
  ExportTooLargeError,
  fetchAllDataViewRows,
} from "@/platform/components/rag/dashboard/data-view/exportDataView";
import { useAuth } from "@/platform/hooks/useAuth";
import { getDataViewTabular } from "@/platform/services/data-view";
import type {
  ColumnSchema,
  DocumentExtractionContext,
} from "@/platform/types/data-view";

export interface KnowledgeMetadata {
  extractionContext?: Record<string, DocumentExtractionContext>;
  columns: ColumnSchema[];
  rows: Record<string, unknown>[];
  /** Distinct documents the walk actually read fields for. */
  documentCount: number;
  /** True when the walk could not cover the whole collection. */
  truncated: boolean;
}

/** Distinct `object_id`s in a metadata result — one document can own several. */
function countDocuments(rows: Record<string, unknown>[]): number {
  const ids = new Set<string>();
  for (const row of rows) {
    const objectId = row?.object_id;
    if (typeof objectId === "string") ids.add(objectId);
  }
  return ids.size;
}

/**
 * Every document's extracted fields, read in one go.
 *
 * The Knowledge screen is one table over the whole collection: the status
 * counts, the facet band, the coverage view and the footer's total are all
 * statements about every document, and none of them can be made from a single
 * server page. So the walk happens once, here, and every control on the screen
 * reads the result — the same thing the Explore screen used to do for its value
 * lists, now shared with the grid instead of duplicated beside it.
 *
 * It walks through `fetchAllDataViewRows`, the CSV export's own reader, rather
 * than paging the v2 route: v2 page numbers **cannot** be walked. Consecutive
 * pages overlap and the walk never reaches part of the set — a 779-point base
 * returns 779 rows over 495 distinct `object_id`s, all a subset of what one v1
 * cursor walk returns (docs/api-flows/data-view.md, re-verified 2026-08-11).
 * Paging v2 here would have left a third of a large collection showing "Not
 * found" in every extracted column.
 *
 * v1 projects a narrower schema than v2 — it drops `file_name`, `source`,
 * `last_modified` and the `clickup_*` keys — which costs this screen nothing:
 * those are document-level facts, and the grid takes them from the documents
 * list instead (see `table/buildTable.ts`).
 */
async function fetchAllMetadata(
  knowledgeId: string,
): Promise<KnowledgeMetadata> {
  let result;
  try {
    result = await fetchAllDataViewRows({
      knowledgeId,
      // No filters and no sort: the screen filters and sorts what it holds, so
      // the walk reads the collection once and every control shares it.
      request: { sort: [] },
      pageSize: METADATA_PAGE_SIZE,
      maxRows: MAX_TABLE_ROWS,
    });
  } catch (error) {
    // A collection past the ceiling is not a failure — the documents and their
    // status are still worth showing. One page of fields comes back, flagged,
    // and the screen says so rather than reporting a total it never read.
    if (!(error instanceof ExportTooLargeError)) throw error;
    const page = await getDataViewTabular(knowledgeId, {
      page: 1,
      page_size: METADATA_PAGE_SIZE,
      sort: [],
    });
    return {
      extractionContext: page.extraction_context,
      columns: page.columns ?? [],
      rows: page.rows ?? [],
      documentCount: countDocuments(page.rows ?? []),
      truncated: true,
    };
  }

  return {
    extractionContext: result.extractionContext,
    columns: result.columns,
    rows: result.rows,
    documentCount: countDocuments(result.rows),
    truncated: result.partial,
  };
}

/** The metadata behind the grid. Idle until a collection is in scope. */
export function useKnowledgeMetadata() {
  const { knowledgeId } = useAuth();

  const query = useQuery({
    queryKey: ["di", "knowledge-metadata", knowledgeId],
    queryFn: () => fetchAllMetadata(knowledgeId as string),
    enabled: Boolean(knowledgeId),
    staleTime: 30_000,
  });

  // A failed metadata read must not take the grid down with it: the document
  // list alone still answers "what is in this collection and did it work",
  // which is the more urgent half. Missing fields show as empty columns.
  return useMemo(
    () => ({
      metadata: query.data,
      isLoading: query.isLoading,
      isFetching: query.isFetching,
      error: query.error,
      refetch: query.refetch,
    }),
    [query.data, query.isLoading, query.isFetching, query.error, query.refetch],
  );
}
