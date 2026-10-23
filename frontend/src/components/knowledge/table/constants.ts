import { isStatusFinal } from "@/platform/components/rag/dashboard/knowledge/utils/fileUtils";

import type { ColumnGroup, TableColumn, VizMode } from "./types";

/**
 * Three groups, in the order the grid draws them.
 *
 * `doc` is locked: the name and how the document arrived are what identify a
 * row, so hiding them would leave a table of values with nothing to attach them
 * to. The other two are the quick axis of the Show control.
 */
export const COLUMN_GROUPS: ColumnGroup[] = [
  {
    key: "doc",
    label: "Document",
    locked: true,
    note: "The document's name and type always stay visible.",
  },
  { key: "status", label: "Status details" },
  { key: "fields", label: "Extracted fields" },
];

/**
 * The document-level columns, which come from the documents list rather than
 * from the extraction schema. `derived` in the type slot is honest: nothing in
 * the service's own schema carries "Added via" or "Fields filled" — the UI
 * computes them.
 */
export const DOC_COLUMNS: TableColumn[] = [
  {
    key: "file",
    label: "Name",
    group: "doc",
    vtype: "text",
    type: "str",
    fixed: true,
  },
  {
    key: "addedvia",
    label: "Added via",
    group: "doc",
    vtype: "cat",
    type: "derived",
    fixed: true,
  },
];

export const STATUS_COLUMNS: TableColumn[] = [
  {
    key: "status",
    label: "Status",
    group: "status",
    vtype: "cat",
    type: "derived",
  },
  {
    key: "created",
    label: "Created",
    group: "status",
    vtype: "date",
    type: "date",
  },
  {
    key: "updated",
    label: "Updated",
    group: "status",
    vtype: "date",
    type: "date",
  },
  {
    key: "filled",
    label: "Fields filled",
    group: "status",
    vtype: "num",
    type: "derived",
  },
];

/** The document columns' keys, so a field column can never collide with one. */
export const DOCUMENT_COLUMN_KEYS = new Set(
  [...DOC_COLUMNS, ...STATUS_COLUMNS].map((column) => column.key),
);

/**
 * Metadata columns the grid already renders from the documents list.
 *
 * The v2 metadata route carries the connector's own keys alongside the
 * extracted fields — `file_name`, `source`, `last_modified` and the rest — and
 * showing them again under "Extracted fields" would claim the extraction found
 * something it did not. Everything not named here is treated as a field.
 */
export const NON_FIELD_COLUMNS = new Set([
  "object_id",
  "file_name",
  "source",
  "status",
  "url",
  "content_type",
  "created_time",
  "updated_time",
  "last_modified",
  "knowledge_id",
  "s3_url",
  "source_url",
  "parent_url",
  "document_processing_mode",
]);

/** How a document reached the collection, as the Added via column prints it. */
export const ADDED_VIA_LABELS: Record<string, string> = {
  clickup: "ClickUp",
  url: "Website",
  plain_text: "Text",
  file: "File",
};

/** The values the type filter offers, in the artifact's order. */
export const ADDED_VIA_OPTIONS = ["Text", "File", "Website", "ClickUp"];

export type StatusBucket = "completed" | "processing" | "failed";

/**
 * The three buckets the status control counts.
 *
 * The service reports a dozen in-flight statuses (`queued`, `indexing`,
 * `enriching`, …); every one of them is a document still being read, so they
 * collapse into one bucket rather than one filter each. Anything final that is
 * not `completed` — `failed`, `invalid` — is a failure; anything else, an
 * absent status included, is still in flight.
 */
export function statusBucket(status?: string | null): StatusBucket {
  const value = (status ?? "").trim().toLowerCase();
  if (value === "completed") return "completed";
  if (isStatusFinal(value)) return "failed";
  return "processing";
}

/** Rows per page before the reader picks a size, matching the artifact. */
export const DEFAULT_PAGE_SIZE = 10;

export const PAGE_SIZE_OPTIONS = [10, 25, 50];

/**
 * How many metadata rows the screen will read before it stops.
 *
 * The whole table is built client-side — the status counts, the facet band, the
 * coverage view and "Showing 8 of 8" are all statements about the *whole*
 * collection, and none of them can be made from one server page. The walk is
 * therefore bounded rather than unbounded, and the screen says so when it stops
 * short instead of quietly reporting a total it did not read.
 */
export const MAX_TABLE_ROWS = 5_000;

/** The largest page either metadata route accepts (422 above it). */
export const METADATA_PAGE_SIZE = 100;

/** The largest page the enrichment-attempt log accepts (`le=200`). */
export const EXTRACTION_LOG_PAGE_SIZE = 200;

/**
 * Where the attempt log walk starts.
 *
 * The route only knows `week` and `month` as periods; an explicit range is the
 * one way to ask for every attempt, and a document processed before this date
 * predates the product.
 */
export const EXTRACTION_LOG_SINCE = "2020-01-01T00:00:00Z";

/** What the Status column prints for each bucket. */
export const STATUS_LABELS: Record<StatusBucket, string> = {
  completed: "Completed",
  processing: "Processing",
  failed: "Failed",
};

/* ── the one table's presentation constants ──────────────────────────────────
 * Colocated here rather than at the top of each component, per the repo's
 * `no-restricted-syntax` rule: a module-level SCREAMING_CASE constant inside a
 * React file is a value someone will want to change without reading JSX.
 */

/** Facet values offered as chips in the band before the panel takes over. */
export const MAX_FACET_CHIPS = 8;

/** Values a donut can still be read at; past this the band shows bars. */
export const MAX_DONUT_SLICES = 8;

/** Bars a chart draws before it says how many values it left out. */
export const MAX_BARS = 14;

/** Entries a list cell shows as chips before it counts the remainder. */
export const MAX_CELL_CHIPS = 3;

/** How the facet band can chart the column it groups by. */
export const VIZ_MODES: Array<{
  key: VizMode;
  label: string;
  title: string;
}> = [
  { key: "bars", label: "Bars", title: "Which values dominate" },
  {
    key: "share",
    label: "Share",
    title: "What proportion of the collection each value is",
  },
  { key: "dist", label: "Distribution", title: "Spread and totals" },
  {
    key: "cover",
    label: "Coverage",
    title: "Which columns the extraction actually filled in",
  },
];

/**
 * Categorical series for the share donut, in draw order.
 *
 * The product's validated series tokens first, then neutrals — read as CSS
 * variables rather than hex, so a donut follows the theme toggle. The status
 * tokens are deliberately not first: a series painted in the danger red would
 * read as a failure.
 */
export const SERIES_COLORS = [
  "var(--di-series-1)",
  "var(--di-series-2)",
  "var(--di-series-3)",
  "var(--di-series-4)",
  "var(--di-accent-strong)",
  "var(--di-warn-dot)",
  "var(--di-success-dot)",
  "var(--di-ink-3)",
];

/** The four buckets the status control offers, with their dot colours. */
export const STATUS_TILES: Array<{
  key: "all" | StatusBucket;
  label: string;
  dot?: string;
  activeText?: string;
  title: string;
}> = [
  { key: "all", label: "All", title: "Show every document" },
  {
    key: "completed",
    label: "Done",
    dot: "bg-success-dot",
    activeText: "text-success-text",
    title: "Show only the documents that have been read",
  },
  {
    key: "processing",
    label: "Reading",
    dot: "bg-warn-dot",
    activeText: "text-warn-text",
    title: "Show only the documents still being read",
  },
  {
    key: "failed",
    label: "Failed",
    dot: "bg-danger-dot",
    activeText: "text-danger-text",
    title: "Show only the documents that could not be read",
  },
];

/**
 * ClickUp sync is hidden for now: no "Sources & sync" tile, no Connected
 * Sources / Configure buttons, no per-row resync. Flip to true to bring the
 * connector back — the feature flag still has the final say.
 */
export const SHOW_CLICKUP_SOURCES = false;

/** Readable width per grid column once the table starts scrolling sideways. */
export const COLUMN_WIDTH = 158;

/** The row-actions cell, which carries no column of its own. */
export const ACTIONS_WIDTH = 80;
