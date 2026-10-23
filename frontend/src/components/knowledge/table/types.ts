import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";
import type { DocumentExtractionContext } from "@/platform/types/data-view";

/**
 * The one table: documents, their processing status and every field pulled out
 * of them, in one grid.
 *
 * Knowledge, Data and Explore used to be three screens over the same rows —
 * the document list, the flattened metadata table and the value browser. They
 * are one screen now, so the types they used to keep apart live here together:
 * a row is a document *and* its extracted values, and a column belongs to one
 * of three groups the header labels.
 */

/** The three column groups the grid's top header row draws. */
export type ColumnGroupKey = "doc" | "status" | "fields";

/**
 * How a column is displayed, filtered, sorted and charted.
 *
 * Deliberately separate from the service's own data type: the field types the
 * product offers are `str`, `int` and `List[str]`, so a date and a headline are
 * both `str` and indistinguishable to it. `vtype` is derived once, from the
 * schema plus the values actually present (see `buildTable`), and everything
 * downstream reads it rather than guessing from a cell.
 */
export type ValueType = "text" | "cat" | "num" | "date" | "list";

export interface TableColumn {
  /** Stable key — a document-level column name, or the service's column name. */
  key: string;
  label: string;
  group: ColumnGroupKey;
  vtype: ValueType;
  /** Printed under the label in the header: the type the service reports. */
  type: string;
  /** Name and Added via anchor the row, so they cannot be switched off. */
  fixed?: boolean;
  /**
   * The schema field an extracted column was flattened from — `line_items` for
   * `line_items_0_amount`. What a document's extraction log is matched on.
   */
  field?: string;
}

export interface ColumnGroup {
  key: ColumnGroupKey;
  label: string;
  /** A locked group is always visible; its eye control is disabled. */
  locked?: boolean;
  note?: string;
}

/** One document: the list route's record, plus every column's value. */
export interface DocumentRow {
  /** API evidence for export; absent/legacy context stays unknown. */
  extractionContext?: DocumentExtractionContext;
  objectId: string;
  /**
   * The document as the list route reports it. The row actions (download the
   * original, download the parsed text, delete) and the detail panel all need
   * the record itself, not just its id.
   */
  document: KnowledgeItem;
  /** Every column's value, keyed by `TableColumn.key`. */
  values: Record<string, unknown>;
  /** How many of the extracted-field columns carry a value in this document. */
  filled: number;
  /**
   * Extracted-field columns this document was never processed against — added
   * to the schema after it was read. Empty ones only: a value on the row always
   * wins, whatever the log says.
   */
  notExtracted: ReadonlySet<string>;
  /** The extracted-field columns this document was asked for: `filled`'s denominator. */
  asked: number;
}

export const FILTER_OPS = [
  "Contains",
  "Does not contain",
  "Equals",
  "Not equals",
  /**
   * Several values of one field, any of which will do.
   *
   * This is what the facet band writes, and it exists because the alternative
   * was unusable: rules are ANDed, so two `Equals` rules on one field asked for
   * documents whose Vendor is both Acme *and* Northwind and answered with an
   * empty table. Picking a second value in a facet UI means "and also this
   * one", every time, so one rule per field holds the whole set.
   */
  "Is any of",
  "Is empty",
  "Is not empty",
  "Greater than",
  "Less than",
] as const;

export type FilterOp = (typeof FILTER_OPS)[number];

/** Operators that decide on presence alone, so their value input is hidden. */
export const VALUELESS_OPS: FilterOp[] = ["Is empty", "Is not empty"];

export interface FilterRule {
  /** Stable across re-renders so a row keeps focus while it is being typed in. */
  id: string;
  field: string;
  op: FilterOp;
  value: string;
  /**
   * The set an `Is any of` rule keeps, held apart from `value` rather than
   * packed into it: a value can itself contain a comma — "Bengaluru, IN" is one
   * location, not two — so any delimiter would eventually split a real value in
   * half.
   */
  values?: string[];
  /**
   * Whether an `Is any of` rule also keeps the documents where the field is
   * empty.
   *
   * Part of the same rule rather than a second `Is empty` one, for the reason
   * the operator exists at all: two rules on one field are ANDed, and "vendor
   * is Acme, or has no vendor" is a single question.
   */
  includeEmpty?: boolean;
}

export interface SortRule {
  field: string;
  /** 1 ascending, -1 descending — the sign the comparator multiplies by. */
  dir: 1 | -1;
}

/** Everything that narrows or orders the grid, in one object. */
export interface TableQuery {
  search: string;
  /** `all`, or one of the Added-via labels. */
  type: string;
  /** `all`, `completed`, `processing` or `failed`. */
  status: string;
  filters: FilterRule[];
  sorts: SortRule[];
}

/** How the facet band charts the column it is grouping by. */
export type VizMode = "bars" | "share" | "dist" | "cover";

/** The four counts the status control shows, over the whole collection. */
export interface StatusCounts {
  all: number;
  completed: number;
  processing: number;
  failed: number;
}

export const EMPTY_QUERY: TableQuery = {
  search: "",
  type: "all",
  status: "all",
  filters: [],
  sorts: [],
};
