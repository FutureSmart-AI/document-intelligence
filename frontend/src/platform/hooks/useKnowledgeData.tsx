import { useCallback, useRef } from "react";

import { useQuery } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { defaultTo, filter, get, isArray, reverse, sortBy } from "lodash-es";

import { useToast } from "@/platform/components/ToastContext";
import {
  uploadService,
  type DocumentProcessingMode,
} from "@/platform/services/upload";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS, CONTENT_TYPES } from "./constants";
import { useAuth } from "./useAuth";
import { useBatchTracking } from "./useBatchTracking";

interface SubUrlItem {
  id: string;
  object_id: string;
  file_name: string | null;
  status: string;
  url: string | null;
  source_url: string | null;
  source_object_id?: string;
  created_time: string;
  updated_at?: string | null;
  content_type: string;
  s3_url?: string;
}

interface KnowledgeItem {
  id: string;
  object_id: string;
  file_name: string;
  status: string;
  url: string | null;
  created_time: string;
  updated_time?: string | null;
  content_type: "plain_text" | "url" | "file" | "clickup";
  s3_url: string;
  source_url?: string | null;
  parent_url: string | null;
  error_detail?: string | null;
  sub_urls?: SubUrlItem[];
  // Where the document came from. Mirrors `content_type` for file/url/plain_text
  // and reads "clickup" for a synced page, which is the only case the two
  // disagree on. Absent on v1 rows and on in-flight batch rows.
  source?: string | null;
  // How the document was read, as chosen on upload. Absent on rows added before
  // the field existed and on in-flight batch rows.
  document_processing_mode?: DocumentProcessingMode | null;
  // Forward-compat: populated for docs synced from ClickUp so a row can be
  // re-indexed individually via the per-doc resync API. Backend does not
  // return these yet — the per-row Resync action stays hidden until it does.
  doc_id?: string | null;
  clickup_doc_id?: string | null;
  // Returned by the v2 document list only: presigned (7-day) URLs for the
  // original upload and for its parsed/extracted markdown rendition. Absent on
  // v1 rows and on in-flight batch rows, where the download actions stay
  // hidden. See list-view/documentLinks.ts.
  source_presigned_url?: string | null;
  extracted_s3_url?: string | null;
  // What processing this document took and cost: parse step, extraction
  // model(s) incl. fallback, embeddings, total ms, estimated cost. Null until the
  // worker records it; absent on in-flight batch rows. See
  // components/knowledge/detail/readProcessing.ts.
  processing_telemetry?: Record<string, unknown> | null;
}

interface KnowledgeData {
  urls: KnowledgeItem[];
  texts: KnowledgeItem[];
  files: KnowledgeItem[];
  allData: KnowledgeItem[];
}

/**
 * Custom structural sharing function that preserves array references
 * if the data hasn't changed (deep equality check on items)
 */
const structuralShareKnowledgeData = (
  oldData: KnowledgeData | undefined,
  newData: KnowledgeData,
): KnowledgeData => {
  if (!oldData) return newData;

  // Check if urls changed
  const urlsChanged = !isArrayEqual(oldData.urls, newData.urls);
  // Check if texts changed
  const textsChanged = !isArrayEqual(oldData.texts, newData.texts);
  // Check if files changed
  const filesChanged = !isArrayEqual(oldData.files, newData.files);

  // If nothing changed, return the old data to preserve reference
  if (!urlsChanged && !textsChanged && !filesChanged) {
    return oldData;
  }

  // Return new object with preserved references for unchanged arrays
  return {
    urls: urlsChanged ? newData.urls : oldData.urls,
    texts: textsChanged ? newData.texts : oldData.texts,
    files: filesChanged ? newData.files : oldData.files,
    allData: newData.allData,
  };
};

/**
 * Efficient array comparison that checks if items have changed
 * based on their identifying fields (object_id, status, etc.)
 */
const isArrayEqual = (
  arr1: KnowledgeItem[],
  arr2: KnowledgeItem[],
): boolean => {
  if (arr1.length !== arr2.length) return false;

  for (let i = 0; i < arr1.length; i++) {
    const item1 = arr1[i];
    const item2 = arr2[i];

    // Compare only the fields that affect UI rendering
    const sub1 = item1.sub_urls ?? [];
    const sub2 = item2.sub_urls ?? [];
    const subUrlsChanged =
      sub1.length !== sub2.length ||
      sub1.some(
        (s, i) =>
          s.status !== sub2[i]?.status || s.object_id !== sub2[i]?.object_id,
      );

    if (
      item1.object_id !== item2.object_id ||
      item1.status !== item2.status ||
      item1.file_name !== item2.file_name ||
      item1.url !== item2.url ||
      item1.s3_url !== item2.s3_url ||
      item1.source_url !== item2.source_url ||
      // Presence, not value: presigned URLs are re-signed periodically and a
      // changed signature must not invalidate the whole list reference.
      !!item1.source_presigned_url !== !!item2.source_presigned_url ||
      !!item1.extracted_s3_url !== !!item2.extracted_s3_url ||
      item1.error_detail !== item2.error_detail ||
      subUrlsChanged
    ) {
      return false;
    }
  }

  return true;
};

/** Both list routes wrap the rows in `document`; tolerate `documents`/bare array. */
const extractDocuments = (payload: unknown): KnowledgeItem[] | null => {
  const raw = isArray(payload)
    ? payload
    : defaultTo(get(payload, "document"), get(payload, "documents"));
  return isArray(raw) ? (raw as KnowledgeItem[]) : null;
};

/**
 * The list view sorts by `created_time` and splits rows by `content_type`, so a
 * payload missing those is unusable as the list even if it carries the URLs.
 */
const hasListShape = (docs: KnowledgeItem[]): boolean =>
  docs.every((doc) => !!doc?.object_id && !!doc?.content_type);

/** Copies the v2-only presigned URLs onto v1 rows, matched on object_id. */
const withDocumentUrls = (
  docs: KnowledgeItem[],
  urlSource: KnowledgeItem[],
): KnowledgeItem[] => {
  const urls = new Map(
    urlSource.map((doc) => [
      doc?.object_id,
      {
        source_presigned_url: doc?.source_presigned_url ?? null,
        extracted_s3_url: doc?.extracted_s3_url ?? null,
      },
    ]),
  );
  return docs.map((doc) => {
    const extra = urls.get(doc.object_id);
    return extra ? { ...doc, ...extra } : doc;
  });
};

export const useKnowledgeData = () => {
  const { toast } = useToast();
  const { knowledgeId } = useAuth();

  // Keep a reference to the previous data for structural sharing
  const previousDataRef = useRef<KnowledgeData | undefined>(undefined);

  const batchIds = useBatchTracking(knowledgeId);

  const fetchKnowledge = useCallback(
    async (
      knowledgeId: string,
      currentBatchIds: string[],
    ): Promise<KnowledgeData> => {
      if (!knowledgeId) {
        throw new Error("Knowledge ID is required");
      }

      try {
        // v2 first: it is the only route carrying the original/parsed presigned
        // URLs. If it is unavailable (or returns rows the list cannot render)
        // fall back to v1 and keep whatever URLs v2 did give us.
        let v2Docs: KnowledgeItem[] | null = null;
        try {
          const v2Response = await axios.get(
            buildApiUrl(APIS.LIST_DOCUMENTS_V2, { knowledge_id: knowledgeId }),
          );
          v2Docs = extractDocuments(v2Response.data);
        } catch {
          v2Docs = null;
        }

        let knowledge: KnowledgeItem[];
        if (v2Docs && hasListShape(v2Docs)) {
          knowledge = v2Docs;
        } else {
          const response = await axios.get(
            buildApiUrl(APIS.LIST_DOCUMENTS, { knowledge_id: knowledgeId }),
          );
          const v1Docs = extractDocuments(response.data);

          if (!v1Docs) {
            toast.error("Invalid knowledge data format");
            throw new Error("Invalid knowledge data format");
          }

          knowledge = v2Docs ? withDocumentUrls(v1Docs, v2Docs) : v1Docs;
        }

        const listDocsMap = new Map<string, boolean>();
        knowledge.forEach((doc: KnowledgeItem) =>
          listDocsMap.set(doc.object_id, true),
        );

        // Fetch temporary batch items
        const batchPromises = currentBatchIds.map((batchId) =>
          uploadService.getBatchStatus(knowledgeId, batchId).catch(() => null),
        );
        const batchResults = await Promise.all(batchPromises);

        const extraDocs: KnowledgeItem[] = [];
        batchResults.forEach((batchRes) => {
          if (batchRes && isArray(batchRes.documents)) {
            batchRes.documents.forEach((doc: Record<string, unknown>) => {
              if (!listDocsMap.has(doc.object_id as string)) {
                listDocsMap.set(doc.object_id as string, true);
                extraDocs.push({
                  id: doc.object_id as string,
                  object_id: doc.object_id as string,
                  file_name: doc.file_name as string,
                  status: doc.status as string,
                  url: (doc.url as string) || null,
                  created_time: new Date().toISOString(),
                  content_type: ((doc.file_name as string) || "").startsWith(
                    "http",
                  )
                    ? "url"
                    : "file",
                  s3_url: "",
                  source_url: (doc.source_url as string) || null,
                  parent_url: null,
                  error_detail: doc.error_detail as string,
                  // Batch status returns object_id/file_name/status/error only —
                  // no download URLs, and a file has no parsed rendition until
                  // processing finishes. Both actions stay hidden on these rows
                  // until the document shows up in the v2 list.
                });
              }
            });
          }
        });

        const mergedKnowledge = [...knowledge, ...extraDocs];

        const newData: KnowledgeData = {
          urls: reverse(
            sortBy(
              filter(mergedKnowledge, { content_type: CONTENT_TYPES.URL }),
              "created_time",
            ),
          ),
          texts: reverse(
            sortBy(
              filter(mergedKnowledge, {
                content_type: CONTENT_TYPES.PLAIN_TEXT,
              }),
              "created_time",
            ),
          ),
          files: reverse(
            sortBy(
              filter(mergedKnowledge, { content_type: CONTENT_TYPES.FILE }),
              "created_time",
            ),
          ),
          allData: reverse(sortBy(mergedKnowledge, "created_time")),
        };

        // Apply structural sharing to preserve references for unchanged data
        const sharedData = structuralShareKnowledgeData(
          previousDataRef.current,
          newData,
        );
        previousDataRef.current = sharedData;

        return sharedData;
      } catch (error: unknown) {
        const status = get(error, "response.status");

        if (status === 503) {
          toast.error(
            "Service temporarily unavailable (503). The backend service might be down or under maintenance. Please try again in a few moments.",
          );
        } else if (status === 500) {
          // Skip showing toast for 500 errors during retries to avoid spam
        } else if (status === 502 || status === 504) {
          toast.error(
            "Gateway error. The backend service might be temporarily unreachable. Please try again shortly.",
          );
        } else {
          const errorMessage = defaultTo(
            get(error, "response.data.detail"),
            defaultTo(
              get(error, "response.data.message"),
              defaultTo(
                get(error, "message"),
                "Failed to fetch knowledge data",
              ),
            ),
          );
          toast.error(errorMessage);
        }
        throw error;
      }
    },
    [toast],
  );

  return useQuery({
    queryKey: ["knowledge-data", knowledgeId, batchIds],
    queryFn: () => fetchKnowledge(knowledgeId!, batchIds),
    enabled: !!knowledgeId,
    retry: (failureCount, error) => {
      if (error instanceof AxiosError) {
        const status = get(error, "response.status");
        const code = get(error, "code");

        if ([404, 401, 403].includes(status || 0)) {
          return false;
        }

        if (status === 503) {
          return false;
        }

        if (code === "ECONNABORTED" && failureCount >= 1) {
          return false;
        }

        if (status === 500 && failureCount >= 2) {
          return false;
        }
      }
      return failureCount < 2;
    },
    retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 5000),
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
    // Enable structural sharing for the query result itself
    structuralSharing: true,
  });
};

// Export types for external use
export type { KnowledgeItem, KnowledgeData, SubUrlItem };
