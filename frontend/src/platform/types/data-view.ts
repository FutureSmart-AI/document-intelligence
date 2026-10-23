/**
 * Data View API Types
 *
 * Types for the tabular data view API that provides flattened document metadata
 */

export type DataType =
  "str" | "int" | "float" | "bool" | "List[str]" | "List[dict]" | "dict";

export interface ColumnSchema {
  /** Flattened column name (e.g., "experience_0_company") */
  name: string;
  /** Original top-level field name from schema (e.g., "experience") */
  original_field: string;
  /** Filter key in dot/bracket notation (e.g., "address.city", "experience[].role") */
  field_path?: string;
  /** Leaf key for nested fields; null for scalar/list_str */
  subcolumn?: string | null;
  /** Parent structure category used by filter/sort UI */
  parent_type?: "scalar" | "list_str" | "dict" | "list_dict";
  /** Field name to be used when sorting */
  sort_field?: string | null;
  /** Data type of the column (legacy field, still returned by some backends) */
  data_type?: DataType;
  /** Whether the column is selected by default/user */
  is_user_selected?: boolean;
}

export type FilterOp =
  | "eq"
  | "ne"
  | "contains"
  | "not_contains"
  | "is_empty"
  | "is_not_empty"
  | "gte"
  | "lte"
  | "gt"
  | "lt"
  | "in";

export interface FilterCondition {
  op: FilterOp;
  /** For `in`, use string[] / number[] matching field type (backend examples use string[]) */
  value?: string | number | string[] | number[];
}

export type FilterValue = FilterCondition;

export interface SortCriteria {
  /** Field name (supports nested fields like location.city) */
  field: string;
  originalField?: string;
  subcolumn?: string;
  /** Sort order */
  order: "asc" | "desc";
}

export interface DataViewRequest {
  /** Number of rows per page */
  page_size?: number;
  /** Page number for pagination */
  page?: number;
  /** Top-level field names to include (e.g., ["experience", "location"]) */
  columns?: string[];
  /** Filters using original field names (e.g., {"location.city": "Bangalore"}) */
  filters?: Record<string, FilterValue>;
  /** Sort criteria */
  sort?: SortCriteria[];
}

/**
 * Cursor-paginated variant of the tabular request (`/v1/…/metadata/all`).
 * Used for CSV export because the v2 `page` field is ignored by the backend.
 */
export interface DataViewCursorRequest {
  /** Rows per request (1-100, backend default 20). */
  limit?: number;
  /** `next_page_offset` from the previous response; omit for the first page. */
  page_offset?: string | null;
  columns?: string[];
  filters?: Record<string, FilterValue>;
  sort?: SortCriteria[];
}

/**
 * v2 tabular request narrowed to a known id set (`object_id in [...]`), used by
 * the CSV export to read the wide v2 schema without walking v2 pagination.
 */
export interface DataViewByObjectIdsRequest {
  /** Documents to read; must fit in a single page of results. */
  objectIds: string[];
  /** Rows per request (backend caps at 100). */
  page_size?: number;
  columns?: string[];
  /** Extra filters ANDed with the id filter — the table's own filters. */
  filters?: Record<string, FilterValue>;
  sort?: SortCriteria[];
}

export interface DocumentExtractionContext {
  status: "recorded" | "unknown";
  event_key: string | null;
  processed_at: string | null;
  processed_fields: string[] | null;
  /** Historical descriptions/types/nested schemas are not in the event log. */
  definition_history: "unknown";
}

export interface DataViewCursorResponse {
  extraction_context?: Record<string, DocumentExtractionContext>;
  columns: ColumnSchema[];
  rows: Record<string, any>[];
  /** Total matching documents, before pagination. */
  total_count: number;
  /** Rows in this response. */
  current_count: number;
  /** Cursor for the next request; null when the last page has been read. */
  next_page_offset?: string | null;
  /** True when more documents remain beyond this response. */
  has_more?: boolean;
}

export interface DataViewResponse {
  extraction_context?: Record<string, DocumentExtractionContext>;
  /** Schema definition for all columns */
  columns: ColumnSchema[];
  /** Data rows (all have identical keys matching column schema) */
  rows: Record<string, any>[];
  /** Total number of matching documents */
  total_count: number;
  /** Number of documents in current response */
  current_count: number;
  /** Current page number */
  current_page: number;
  /** Total number of pages */
  total_pages: number;
}

export interface DataViewError {
  detail: string;
}
