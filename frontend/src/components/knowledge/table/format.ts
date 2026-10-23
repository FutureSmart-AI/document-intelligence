import dayjs from "dayjs";

import type { DocumentRow, TableColumn } from "./types";

/**
 * One accessor and one text formatter for every column, derived ones included,
 * so the cells, the filters, the sorts, the facet band, the charts and the CSV
 * all read the same value. Three screens reading the same rows through three
 * different accessors is how the old Knowledge list and Data table came to
 * disagree about what a document's type was.
 */
export function cellValue(row: DocumentRow, key: string): unknown {
  return row.values[key];
}

/** `experience_0_company` → `Experience #1 Company`. */
export function toColumnLabel(name: string): string {
  return name
    .split(/[_.]/)
    .filter(Boolean)
    .map((part) =>
      /^\d+$/.test(part)
        ? `#${Number(part) + 1}`
        : part.charAt(0).toUpperCase() + part.slice(1),
    )
    .join(" ");
}

/** True when a cell has nothing in it — an empty list counts as nothing. */
export function isEmptyValue(value: unknown): boolean {
  if (Array.isArray(value)) return value.length === 0;
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  return false;
}

const DATE_FORMAT = "MMM D, YYYY";

/** What a date column prints. Unparseable values are shown as they arrived. */
export function formatDate(value: unknown): string {
  if (typeof value !== "string" && typeof value !== "number") return "";
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format(DATE_FORMAT) : String(value);
}

/**
 * The cell's text — also what the search, the CSV and a facet label use, so a
 * value reads the same everywhere it appears.
 */
export function cellText(column: TableColumn, value: unknown): string {
  if (isEmptyValue(value)) return "";
  if (column.vtype === "date") return formatDate(value);
  if (Array.isArray(value))
    return value.map((entry) => String(entry)).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** The number a numeric column sorts and charts by; `null` when there is none. */
export function cellNumber(column: TableColumn, value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (Array.isArray(value)) return value.length;
  const match = String(value ?? "")
    .replace(/,/g, "")
    .match(/-?\d+(\.\d+)?/);
  return match ? Number.parseFloat(match[0]) : null;
}

/**
 * A short number for the histogram's stat cells and bucket labels: two decimals
 * at most, and two significant digits below one — a fixed one decimal printed
 * a column of per-mile rates as 0 and 0.2.
 */
export function formatStat(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const short =
    Math.abs(value) < 1
      ? Number(value.toPrecision(2))
      : Math.round(value * 100) / 100;
  return String(short);
}
