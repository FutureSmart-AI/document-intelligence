import { cellNumber, cellText, formatStat, isEmptyValue } from "./format";
import { filterRows } from "./selectRows";
import type { DocumentRow, TableColumn, TableQuery } from "./types";

/**
 * The facet band — what the Explore screen used to be.
 *
 * It sits above the table as a band rather than beside it as a rail, and a
 * clicked value writes a real filter rule instead of keeping a second,
 * invisible filter of its own. That is what makes "why am I seeing three rows"
 * answerable from the Filters panel, and what makes the selection survive an
 * export.
 */
export interface FacetValue {
  /** The value as text; empty when this is the "no value" bucket. */
  value: string;
  /** True for the bucket counting documents where the field is empty. */
  missing: boolean;
  count: number;
}

export interface FacetResult {
  values: FacetValue[];
  /** Documents the counts were taken over — the sum for a single-valued field. */
  base: number;
}

const MISSING_KEY = " none";

/** Only reached when a facet is asked for a column that is no longer there. */
const FALLBACK: TableColumn = {
  key: "",
  label: "",
  group: "fields",
  vtype: "text",
  type: "str",
};

/**
 * Every column, offered as something to group by.
 *
 * This used to drop the free-prose columns, on the reasoning that grouping by a
 * summary gives one bucket per document and says nothing. That reasoning holds
 * for a summary and fails for everything the type guess gets wrong: a display
 * type is inferred from the values present, so a field with 30 distinct answers
 * across 40 documents reads as prose and vanished from the picker with no way
 * to ask for it back. Whether a column is worth grouping by is the reader's
 * call, and a one-bucket-per-row chart says so plainly the moment it is drawn.
 */
export function facetableColumns(columns: TableColumn[]): TableColumn[] {
  return columns;
}

export function facetCounts(
  rows: DocumentRow[],
  columns: TableColumn[],
  field: string,
  query: TableQuery,
): FacetResult {
  const column = columns.find((candidate) => candidate.key === field);
  // The field's own rules are ignored so its sibling values stay reachable.
  const scope = filterRows(rows, columns, query, field);
  const counts = new Map<string, number>();

  for (const row of scope) {
    const raw = row.values[field];
    const entries =
      Array.isArray(raw) && raw.length
        ? raw.map((entry) => String(entry))
        : [isEmptyValue(raw) ? "" : cellText(column ?? FALLBACK, raw)];
    for (const entry of entries) {
      const key = entry === "" ? MISSING_KEY : entry;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
  }

  const values: FacetValue[] = [...counts.entries()]
    .map(([key, count]) => ({
      value: key === MISSING_KEY ? "" : key,
      missing: key === MISSING_KEY,
      count,
    }))
    .sort(
      (a, b) =>
        b.count - a.count ||
        // "(no value)" is not a value, so it goes last among equal counts
        // rather than winning the tie on an empty string sorting first.
        Number(a.missing) - Number(b.missing) ||
        a.value.localeCompare(b.value),
    );

  return { values, base: scope.length };
}

export function facetValueLabel(facet: FacetValue): string {
  return facet.missing ? "(no value)" : facet.value;
}

export interface VizItem {
  label: string;
  count: number;
}

/**
 * Coverage — the view that justifies the product: which columns the extraction
 * actually filled in, and which one is thinnest.
 */
export function coverageItems(
  rows: DocumentRow[],
  columns: TableColumn[],
): VizItem[] {
  return columns
    .filter(
      (column) =>
        column.key !== "file" &&
        column.key !== "addedvia" &&
        column.key !== "status",
    )
    .map((column) => ({
      label: column.label,
      count: rows.filter((row) => !isEmptyValue(row.values[column.key])).length,
    }))
    .sort((a, b) => a.count - b.count || a.label.localeCompare(b.label));
}

export interface Histogram {
  buckets: VizItem[];
  count: number;
  min: number;
  max: number;
  mean: number;
  median: number;
  sum: number;
}

/** Spread and totals for a numeric column. `null` when it holds no numbers. */
export function histogram(
  rows: DocumentRow[],
  column: TableColumn,
): Histogram | null {
  const values = rows
    .map((row) => cellNumber(column, row.values[column.key]))
    .filter((value): value is number => value != null)
    .sort((a, b) => a - b);
  if (!values.length) return null;

  const min = values[0];
  const max = values[values.length - 1];
  const sum = values.reduce((total, value) => total + value, 0);
  const middle = Math.floor(values.length / 2);
  const median =
    values.length % 2
      ? values[middle]
      : (values[middle - 1] + values[middle]) / 2;

  const bucketCount = Math.min(
    6,
    Math.max(3, Math.ceil(Math.sqrt(values.length))),
  );
  const span = max - min || 1;
  const width = span / bucketCount;
  const buckets: VizItem[] = Array.from(
    { length: bucketCount },
    (_, index) => ({
      label: `${formatStat(min + index * width)}–${formatStat(min + (index + 1) * width)}`,
      count: 0,
    }),
  );
  for (const value of values) {
    const index = Math.min(bucketCount - 1, Math.floor((value - min) / width));
    buckets[index].count += 1;
  }

  return {
    buckets,
    count: values.length,
    min,
    max,
    sum,
    mean: sum / values.length,
    median,
  };
}
