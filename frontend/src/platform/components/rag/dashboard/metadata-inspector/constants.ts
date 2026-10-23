import type { MetadataSectionConfig } from "./types";

export const DEFAULT_DOCUMENT_CONFIG: MetadataSectionConfig = {
  title: "Document Overview",
  description: "High-level attributes returned for this item.",
  emptyMessage: "No document metadata available yet.",
  loadingMessage: "Loading document metadata...",
  rawJsonLabel: "View raw document JSON",
};

export const DEFAULT_CHUNK_CONFIG: MetadataSectionConfig = {
  title: "Chunk Insights",
  description: "Detailed breakdown of the processed chunks.",
  emptyMessage: "No chunk-level metadata available yet.",
  loadingMessage: "Loading chunk metadata...",
  rawJsonLabel: "View raw chunk JSON",
};
