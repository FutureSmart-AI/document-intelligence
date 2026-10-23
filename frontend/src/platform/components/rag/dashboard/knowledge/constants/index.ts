import type { DocumentProcessingMode } from "@/platform/services/upload";

// Form field names for the enrichment LLM selection, shared by the config modal
// and the config page.
export const ENRICHMENT_PROVIDER_FIELD = "enrichment_provider";
export const ENRICHMENT_MODEL_FIELD = "enrichment_model";

export const DEFAULT_CONFIG = {
  embedding_model: "paraphrase-multilingual-mpnet-base-v2",
  hybrid_search_enabled: true,
  chunking_strategy: {
    document_level_metadata: true,
    chunk_level_metadata: false,
  },
  service_description: "",
  // Filled in from the backend defaults once metadata enrichment is enabled.
  [ENRICHMENT_PROVIDER_FIELD]: "",
  [ENRICHMENT_MODEL_FIELD]: "",
} as const;

/**
 * The five document-processing options offered on the file upload form, sent as
 * `document_processing_mode`. Each one asks what the documents *are* — a
 * question the person uploading them can answer — and the service picks the
 * parser from it. Cost rises down the list, which is why text-based is the
 * default and the one the service assumes when the field is left out.
 */
export const DOC_PROCESSING_OPTIONS: ReadonlyArray<{
  value: DocumentProcessingMode;
  label: string;
  desc: string;
}> = [
  {
    value: "text_based",
    label: "Text-based",
    desc: "Information can be extracted directly from the available text.",
  },
  {
    value: "scanned",
    label: "Scanned",
    desc: "Text needs to be read from images using OCR.",
  },
  {
    value: "layout_aware",
    label: "Layout-aware",
    desc: "Meaning depends on the position and relationship between text, tables, fields, or columns.",
  },
  {
    value: "visual",
    label: "Visual",
    desc: "Important information exists in charts, diagrams, images, or other visual elements.",
  },
  {
    value: "mixed",
    label: "Mixed",
    desc: "The files contain more than one of these content types.",
  },
];
