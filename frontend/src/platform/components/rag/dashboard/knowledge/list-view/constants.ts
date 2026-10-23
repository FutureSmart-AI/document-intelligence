import { DOC_PROCESSING_OPTIONS } from "../constants";

export const TYPE_BADGE_STYLES = {
  plain_text: "bg-purple-50 text-purple-700 ring-purple-600/20",
  file: "bg-blue-50 text-blue-700 ring-blue-600/20",
  url: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  clickup: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
};

export const TYPE_LABELS = {
  plain_text: "Text",
  file: "File",
  url: "Website",
  clickup: "ClickUp",
};

export const STATUS_STYLES: Record<string, string> = {
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  processing: "bg-blue-50 text-blue-700 ring-blue-200",
  enriching: "bg-blue-50 text-blue-700 ring-blue-200",
  pending: "bg-blue-50 text-blue-700 ring-blue-200",
  queued: "bg-blue-50 text-blue-700 ring-blue-200",
  indexing: "bg-blue-50 text-blue-700 ring-blue-200",
};

/** Rows shown per page in the knowledge list before the user picks a size. */
export const KNOWLEDGE_DEFAULT_PAGE_SIZE = 10;

/**
 * Where a document came from, as the documents API reports it on `source`.
 * The values are the same four `content_type` uses — they only ever disagree on
 * a ClickUp-synced page — so the source column reuses the content-type
 * labels/styles the delete dialog and detail panel already share.
 */
export const SOURCE_LABELS = TYPE_LABELS;
export const SOURCE_BADGE_STYLES = TYPE_BADGE_STYLES;

/** Options for the list's source filter — the values `source` can hold. */
export const SOURCE_OPTIONS = [
  { label: "Text", value: "plain_text" },
  { label: "File", value: "file" },
  { label: "Website", value: "url" },
  { label: "ClickUp", value: "clickup" },
] as const;

/**
 * How a document was read, as the documents API reports it on
 * `document_processing_mode`. Labels come from the upload form's own options so
 * a row reads back exactly as the person uploading it chose.
 */
export const PROCESSING_MODE_LABELS: Record<string, string> =
  Object.fromEntries(DOC_PROCESSING_OPTIONS.map((o) => [o.value, o.label]));
