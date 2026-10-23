import { browserStorageUrl } from "@/lib/storageUrl";
import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";

/**
 * Resolves the two downloadable artifacts of an uploaded knowledge item, both
 * served by the v2 document list as presigned HTTPS URLs (7-day expiry,
 * re-signed server-side once under a day remains):
 *
 * - `originalUrl` — the file exactly as the user uploaded it, from
 *   `source_presigned_url`, falling back to v1's presigned `source_url`.
 *   `s3_url` is deliberately never used: on v1 it can be a bare `s3://` key,
 *   which the browser cannot fetch.
 * - `parsedUrl` — the parsed/extracted (markdown) rendition produced by the
 *   ingestion pipeline, from `extracted_s3_url`. Always HTTPS on v2. Absent
 *   until the document finishes processing, so the action stays hidden for
 *   in-flight uploads.
 */
export interface DocumentLinks {
  originalUrl: string | null;
  parsedUrl: string | null;
}

/** Only browser-fetchable URLs count — a bare `s3://` key is not one. */
const firstUrl = (...candidates: Array<unknown>): string | null => {
  for (const candidate of candidates) {
    if (typeof candidate !== "string") continue;
    const value = candidate.trim();
    if (/^https?:\/\//i.test(value)) return browserStorageUrl(value);
  }
  return null;
};

export const resolveDocumentLinks = (item: KnowledgeItem): DocumentLinks => ({
  originalUrl: firstUrl(item.source_presigned_url, item.source_url),
  parsedUrl: firstUrl(item.extracted_s3_url),
});

/** Suggested filename for the parsed rendition (markdown output). */
export const parsedFileName = (fileName: string): string => {
  const base = fileName?.trim() || "document";
  const dot = base.lastIndexOf(".");
  const stem = dot > 0 ? base.slice(0, dot) : base;
  return `${stem}.parsed.md`;
};
