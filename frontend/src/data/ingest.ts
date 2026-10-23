"use client";

import {
  DEFAULT_MAX_DEPTH,
  type MaxDepth,
  type ScrapeMethod,
} from "@/lib/constants";
import { addBatchToTracking } from "@/platform/hooks/useBatchTracking";
import { getContentType, uploadService } from "@/platform/services/upload";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * Adding documents to a collection.
 *
 * **What each source sends, and nothing beyond it** — that is the add-document
 * contract, not a shortcut:
 *
 * | Source     | Sent                                        |
 * | ---------- | ------------------------------------------- |
 * | Files      | nothing                                     |
 * | Website    | `scraping_method`, `max_depth` (crawl only) |
 * | Plain text | nothing                                     |
 *
 * `document_processing_mode` is gone as well as `scrape_using`. The service
 * detects how a document has to be read — text, scanned, layout-heavy, visual
 * or mixed — and re-reads an image-only PDF by itself, so the mode has a
 * server-side default of auto and there is nothing left for a person to pick
 * wrong. Sending it would pin a guess made in a dialog over a decision the
 * service makes with the document in hand. (product review, 2026-09-01.)
 *
 * `enable_chunking`, `chunk_size`, `transcript_provider`, `use_llama_parse` and
 * `extra_metadata` are all left out for the same reason. Each has a
 * server-side default (chunking on, 2000 tokens, OpenAI transcripts),
 * and leaving them out is what lets those defaults be tuned without a UI
 * release — a client that sends them freezes today's values in. None of them is
 * a choice this product asks anyone to make.
 *
 * A field sent to the wrong source is **ignored, not rejected**: a crawl depth on
 * an upload, or a processing mode on pasted text, answers 200 and quietly does
 * nothing. So a mis-wired panel produces no error anywhere, which is why each
 * function below takes only the fields its own source can use.
 */

/**
 * Plain text: `POST /documents` with the text, and nothing else. Answers
 * `{object_id, status: "processing"}` straight away.
 */
export async function ingestPlainText(knowledgeId: string, text: string) {
  const body = new FormData();
  body.append("text", text);

  const response = await axios.post(
    buildApiUrl(APIS.ADD_DOCUMENT, { knowledge_id: knowledgeId }),
    body,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return response.data;
}

/**
 * A website: the link, whether we read the one page or follow its links, and how
 * far a crawl goes.
 *
 * Neither `scrape_using` nor `document_processing_mode` is sent: the first is
 * deprecated and has no effect, and the second the service now decides itself
 * from the page it fetched — a URL that resolves to a PDF or DOCX is read
 * exactly like an uploaded one.
 *
 * `max_depth` rides with a crawl and only a crawl: a scrape reads the page it
 * was given, so a depth beside it means nothing and is left out so the service
 * keeps its own default of 1.
 *
 * `scraping_method` is lowercase on the wire even where the control reads
 * "Scrape", so it is passed through from `lib/constants` rather than spelled out
 * at the call site — the wrong case is a 422 at upload time.
 */
export async function ingestWebsite(
  knowledgeId: string,
  url: string,
  options?: {
    method?: ScrapeMethod;
    maxDepth?: MaxDepth;
  },
) {
  const method: ScrapeMethod = options?.method ?? "scrape";
  const body = new FormData();
  body.append("weblink", url);
  body.append("scraping_method", method);
  if (method === "crawl") {
    body.append("max_depth", String(options?.maxDepth ?? DEFAULT_MAX_DEPTH));
  }

  const response = await axios.post(
    buildApiUrl(APIS.ADD_DOCUMENT, { knowledge_id: knowledgeId }),
    body,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return response.data;
}

/** A file that never reached S3, with the reason it did not. */
export interface FailedUpload {
  fileName: string;
  error: string;
}

/** A file the service took but could not read, with its own reason. */
export interface InvalidDocument {
  fileName: string;
  errorDetail?: string | null;
}

export interface FilesIngested {
  /** Null when every file failed to reach S3, so nothing was registered. */
  batchId: string | null;
  failed: FailedUpload[];
  invalid: InvalidDocument[];
}

function uploadError(error: unknown): string {
  const response = (error as { response?: { data?: Record<string, unknown> } })
    ?.response?.data;
  const detail = response?.detail ?? response?.message;
  if (typeof detail === "string") return detail;
  const message = (error as { message?: string })?.message;
  return message || "Upload failed";
}

/**
 * Files: presign the batch, put each file to S3, then register them.
 *
 * **Every upload goes through the batch endpoints, one file or fifty.** The
 * single-file presign and the direct-multipart routes exist on the service, but
 * this product does not call them — a one-file upload answers
 * `{batch_id, document_statuses}` like any other batch, so the list has one
 * shape to track and the caller has one shape to report.
 *
 * The batch carries the file addresses and nothing else — how each document has
 * to be read is the service's own reading of it, so scans and text can go up
 * side by side in one upload.
 *
 * S3 puts run in parallel and are allowed to fail individually — a folder with
 * one unreadable file still registers the rest, and the names that failed come
 * back so the screen can say which.
 */
export async function ingestFiles(
  knowledgeId: string,
  files: File[],
  options?: {
    onProgress?: (percent: number) => void;
  },
): Promise<FilesIngested> {
  if (!files.length) return { batchId: null, failed: [], invalid: [] };

  const requested = files.map((file) => ({
    file_name: file.name,
    content_type: getContentType(file),
  }));

  const batch = await uploadService.getBatchPresignedUrls({
    knowledgeId,
    files: requested,
  });
  const uploads = batch?.uploads ?? [];
  if (!uploads.length) throw new Error("No upload URLs received from server");

  // The response names each upload rather than indexing it, so the files are
  // matched back by name — the same way the platform's own upload does.
  const byName = new Map(files.map((file) => [file.name, file]));

  const percents = new Array(uploads.length).fill(0);
  const results = await Promise.all(
    uploads.map(async (upload, index) => {
      const file = byName.get(upload.file_name);
      if (!file) {
        return {
          fileName: upload.file_name,
          error: "File not found in selection",
        };
      }
      try {
        await uploadService.uploadToS3(
          upload.presigned_url,
          file,
          (percent) => {
            percents[index] = percent;
            const total = percents.reduce((sum, value) => sum + value, 0);
            options?.onProgress?.(Math.round(total / uploads.length));
          },
        );
        // The presigned URL's signature is not part of the document's address.
        return { fileUrl: upload.download_presigned_url.split("?")[0] };
      } catch (error) {
        return { fileName: upload.file_name, error: uploadError(error) };
      }
    }),
  );

  const fileUrls = results
    .map((result) => ("fileUrl" in result ? result.fileUrl : null))
    .filter((url): url is string => Boolean(url));
  const failed = results
    .filter((result): result is FailedUpload => "error" in result)
    .map((result) => ({ fileName: result.fileName, error: result.error }));

  if (!fileUrls.length) {
    return { batchId: batch.batch_id ?? null, failed, invalid: [] };
  }

  // Register them. `file_urls` is the only field this source sends — the
  // service works out how to read each one.
  const body = new FormData();
  body.append("file_urls", fileUrls.join(","));

  const registered = await axios.post<{
    documents?: Array<{
      file_name?: string;
      status?: string;
      error_detail?: string | null;
    }>;
  }>(
    buildApiUrl(APIS.ADD_DOCUMENTS_BATCH, { knowledge_id: knowledgeId }),
    body,
    { headers: { "Content-Type": "multipart/form-data" } },
  );

  // Tracking the batch is what puts the documents in the list before the first
  // one has finished being read — the list reads its tracked batches' statuses
  // alongside the documents route.
  if (batch.batch_id) addBatchToTracking(knowledgeId, batch.batch_id);

  const invalid = (registered.data?.documents ?? [])
    .filter((document) => document?.status === "invalid")
    .map((document) => ({
      fileName: document.file_name ?? "This file",
      errorDetail: document.error_detail ?? null,
    }));

  return { batchId: batch.batch_id ?? null, failed, invalid };
}
