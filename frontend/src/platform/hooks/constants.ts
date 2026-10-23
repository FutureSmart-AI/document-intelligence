// Constants
export const CONTENT_TYPES = {
  URL: "url",
  PLAIN_TEXT: "plain_text",
  FILE: "file",
} as const;

export const APIS = {
  // v2 is the list route the Knowledge Upload Status window reads: same rows as
  // v1 plus `source_presigned_url` (original) and `extracted_s3_url` (parsed /
  // extracted markdown), both presigned for 7 days. v1 is kept as a fallback
  // for deployments where v2 is not live yet.
  LIST_DOCUMENTS_V2: "/api/knowledge/v2/{knowledge_id}/documents",
  LIST_DOCUMENTS: "/api/knowledge/v1/{knowledge_id}/documents",
} as const;
