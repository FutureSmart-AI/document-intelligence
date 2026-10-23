"use client";

import { useMemo } from "react";

import { useQuery } from "@tanstack/react-query";

import {
  EXTRACTION_LOG_PAGE_SIZE,
  EXTRACTION_LOG_SINCE,
  MAX_TABLE_ROWS,
} from "@/components/knowledge/table/constants";
import { useAuth } from "@/platform/hooks/useAuth";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * Which fields each document was actually processed against.
 *
 * The metadata table is projected through the collection's schema *as it is
 * today*, with `null` for every column a document has no value in — so a field
 * added last week reads as "Not found" on every document processed before it,
 * which looks exactly like an extraction that failed. The enrichment log is
 * what knows the difference: every attempt records `field_data`, the fields it
 * was asked for (populated or not). That list is the schema version a document
 * was processed under.
 *
 * Verified against the live service 2026-09-24: `field_data` names every field
 * of the attempt including the empty ones, and the document's stored payload
 * carries nothing outside it. The payload itself is no substitute — most empty
 * fields are simply omitted from it, so key absence cannot tell "asked, found
 * nothing" from "never asked".
 */

/** One attempt, as far as this screen reads it. */
export interface ExtractionAttempt {
  object_id?: string | null;
  created_at?: string | null;
  status?: string | null;
  field_data?: Array<{ field_name?: string | null }> | null;
}

/**
 * Document id → the top-level field names its latest attempt was asked for.
 *
 * Most recent because a reprocess re-extracts against the schema of its day
 * and replaces the stored values. A successful attempt wins over a later
 * failure: a failed retry does not wipe what the last success stored, so the
 * values on screen are still that success's. An attempt with no `field_data`
 * says nothing about the schema and is skipped rather than read as "no fields".
 */
export function extractedFieldsByDocument(
  attempts: ExtractionAttempt[],
): Map<string, Set<string>> {
  const latest = new Map<
    string,
    { at: string; success: boolean; fields: Set<string> }
  >();
  for (const attempt of attempts) {
    const objectId = attempt?.object_id;
    if (!objectId || !Array.isArray(attempt.field_data)) continue;
    const fields = new Set(
      attempt.field_data
        .map((field) => field?.field_name)
        .filter((name): name is string => Boolean(name)),
    );
    if (!fields.size) continue;
    const at = attempt.created_at ?? "";
    const success = attempt.status === "success";
    const held = latest.get(objectId);
    const newer = !held || at > held.at;
    if (
      !held ||
      (success && !held.success) ||
      (success === held.success && newer)
    )
      latest.set(objectId, { at, success, fields });
  }
  return new Map(
    [...latest].map(([objectId, entry]) => [objectId, entry.fields]),
  );
}

interface AttemptPage {
  total_matching?: number;
  documents?: ExtractionAttempt[];
}

async function fetchAllAttempts(knowledgeId: string) {
  const url = buildApiUrl(APIS.HEALTH_ENRICHMENT_DOCUMENTS, {
    knowledge_id: knowledgeId,
  });
  // Tomorrow, so an attempt logged while the walk runs is still inside it.
  const until = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
  const attempts: ExtractionAttempt[] = [];
  // Bounded like the metadata walk: a document can own several attempts, so
  // twice the row ceiling is room for a retry each without an unbounded loop.
  for (let offset = 0; offset < MAX_TABLE_ROWS * 2;) {
    const response = await axios.get<AttemptPage>(url, {
      params: {
        start_date: EXTRACTION_LOG_SINCE,
        end_date: until,
        include_field_data: true,
        limit: EXTRACTION_LOG_PAGE_SIZE,
        offset,
      },
    });
    const page = response.data?.documents ?? [];
    attempts.push(...page);
    offset += page.length;
    const total = response.data?.total_matching ?? 0;
    if (page.length < EXTRACTION_LOG_PAGE_SIZE || offset >= total) break;
  }
  return extractedFieldsByDocument(attempts);
}

/**
 * The processed-under field set of every document in the collection.
 *
 * Failure is quiet on purpose: without the log the table falls back to what it
 * showed before — today's schema on every row — which is less precise but not
 * wrong about any value. It is not worth an error band over the grid.
 */
export function useExtractedFields() {
  const { knowledgeId } = useAuth();
  const query = useQuery({
    queryKey: ["di", "extracted-fields", knowledgeId],
    queryFn: () => fetchAllAttempts(knowledgeId as string),
    enabled: Boolean(knowledgeId),
    retry: false,
    staleTime: 30_000,
  });
  return useMemo(
    () => ({ extractedFields: query.data, refetch: query.refetch }),
    [query.data, query.refetch],
  );
}
