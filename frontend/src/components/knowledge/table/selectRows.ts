import { statusBucket } from "./constants";
import { cellNumber, cellText, isEmptyValue } from "./format";
import type {
  DocumentRow,
  FilterRule,
  SortRule,
  StatusCounts,
  TableColumn,
  TableQuery,
} from "./types";

/** Column lookup by key — every helper here needs one. */
export function columnIndex(columns: TableColumn[]): Map<string, TableColumn> {
  return new Map(columns.map((column) => [column.key, column]));
}

/** Everything in a row that the search box searches: every column's text. */
export function rowHaystack(row: DocumentRow, columns: TableColumn[]): string {
  const parts = [row.objectId];
  for (const column of columns) {
    const text = cellText(column, row.values[column.key]);
    if (text) parts.push(text);
  }
  return parts.join(" ").toLowerCase();
}

/** One filter rule against one row. */
export function matchesRule(
  row: DocumentRow,
  rule: FilterRule,
  columns: Map<string, TableColumn>,
): boolean {
  const column = columns.get(rule.field);
  if (!column) return true;
  const raw = row.values[rule.field];
  const empty = isEmptyValue(raw);
  if (rule.op === "Is empty") return empty;
  if (rule.op === "Is not empty") return !empty;

  /**
   * A set of values, any of which will do — what the facet band writes.
   *
   * The empties are part of the same rule rather than a second one, because two
   * rules on one field are ANDed and "vendor is Acme, or has none" is one
   * question. A rule holding nothing at all keeps every row, the same way a
   * half-typed `Contains` does: an empty rule is a rule not finished, not a
   * rule that excludes everything.
   */
  if (rule.op === "Is any of") {
    const wanted = (rule.values ?? []).map((entry) => entry.toLowerCase());
    if (!wanted.length && !rule.includeEmpty) return true;
    if (empty) return Boolean(rule.includeEmpty);
    if (!wanted.length) return false;
    return Array.isArray(raw)
      ? raw.some((entry) => wanted.includes(String(entry).toLowerCase()))
      : wanted.includes(cellText(column, raw).toLowerCase());
  }

  const needle = rule.value.trim();
  // A rule with nothing typed in it yet must not empty the table.
  if (!needle) return true;

  if (rule.op === "Greater than" || rule.op === "Less than") {
    const value = cellNumber(column, raw);
    const bound = Number.parseFloat(needle);
    if (value == null || !Number.isFinite(bound)) return false;
    return rule.op === "Greater than" ? value > bound : value < bound;
  }

  const text = cellText(column, raw).toLowerCase();
  const target = needle.toLowerCase();
  switch (rule.op) {
    case "Contains":
      return text.includes(target);
    case "Does not contain":
      return !text.includes(target);
    case "Equals":
      // A list matches when any one of its entries is the value, so clicking
      // "Python" in the facet band for a skills column keeps every document
      // that lists it rather than only those that list nothing else.
      return Array.isArray(raw)
        ? raw.some((entry) => String(entry).toLowerCase() === target)
        : text === target;
    case "Not equals":
      return Array.isArray(raw)
        ? !raw.some((entry) => String(entry).toLowerCase() === target)
        : text !== target;
    default:
      return true;
  }
}

/**
 * The rows the query keeps.
 *
 * `ignoreField` is what lets the facet band stay usable once a value is
 * selected: the counts for the field being grouped by ignore that field's own
 * rules, so the sibling values are still there to pivot to instead of the band
 * collapsing to the one value you just clicked.
 */
export function filterRows(
  rows: DocumentRow[],
  columns: TableColumn[],
  query: TableQuery,
  ignoreField?: string,
): DocumentRow[] {
  const index = columnIndex(columns);
  const needle = query.search.trim().toLowerCase();
  const rules = ignoreField
    ? query.filters.filter((rule) => rule.field !== ignoreField)
    : query.filters;

  return rows.filter((row) => {
    if (needle && !rowHaystack(row, columns).includes(needle)) return false;
    if (
      query.type !== "all" &&
      String(row.values.addedvia ?? "") !== query.type
    )
      return false;
    if (
      query.status !== "all" &&
      statusBucket(row.document.status) !== query.status
    )
      return false;
    return rules.every((rule) => matchesRule(row, rule, index));
  });
}

/** Multi-rule sort: rule order is precedence, and the first difference wins. */
export function sortRows(
  rows: DocumentRow[],
  columns: TableColumn[],
  sorts: SortRule[],
): DocumentRow[] {
  if (!sorts.length) return rows;
  const index = columnIndex(columns);
  return rows.slice().sort((left, right) => {
    for (const rule of sorts) {
      const column = index.get(rule.field);
      if (!column) continue;
      const a = left.values[rule.field];
      const b = right.values[rule.field];
      let comparison = 0;
      if (column.vtype === "num") {
        // A row with no number sorts below every row that has one, in both
        // directions — "nothing extracted" is not a small value.
        const an = cellNumber(column, a);
        const bn = cellNumber(column, b);
        if (an == null && bn == null) comparison = 0;
        else if (an == null) comparison = 1;
        else if (bn == null) comparison = -1;
        else comparison = (an - bn) * rule.dir;
      } else if (column.vtype === "date") {
        const at = Date.parse(String(a ?? "")) || 0;
        const bt = Date.parse(String(b ?? "")) || 0;
        comparison = (at - bt) * rule.dir;
      } else {
        comparison =
          cellText(column, a).localeCompare(cellText(column, b), undefined, {
            numeric: true,
          }) * rule.dir;
      }
      if (comparison !== 0) return comparison;
    }
    return 0;
  });
}

export function selectRows(
  rows: DocumentRow[],
  columns: TableColumn[],
  query: TableQuery,
): DocumentRow[] {
  return sortRows(filterRows(rows, columns, query), columns, query.sorts);
}

/** Where `field` sits in the sort order, or -1 when it takes no part. */
export function sortRank(sorts: SortRule[], field: string): number {
  return sorts.findIndex((rule) => rule.field === field);
}

/**
 * The status counts, over the whole collection rather than the filtered view.
 *
 * "All 8 / Done 6 / Reading 1 / Failed 1" is a statement about the collection,
 * and it is how someone finds the one document that failed — so narrowing it by
 * the search box would hide exactly the row it exists to surface. What is in
 * view is the footer's job.
 */
export function statusCounts(rows: DocumentRow[]): StatusCounts {
  const counts: StatusCounts = {
    all: rows.length,
    completed: 0,
    processing: 0,
    failed: 0,
  };
  for (const row of rows) counts[statusBucket(row.document.status)] += 1;
  return counts;
}
