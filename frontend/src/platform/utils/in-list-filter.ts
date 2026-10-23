import type { DataType } from "@/platform/types/data-view";

/**
 * Parse free-form user input into list items for filter `op: "in"`.
 * Accepts comma-, semicolon-, or newline-separated values.
 */
export function parseInListInput(raw: string): string[] {
  const normalized = raw.replace(/\r\n/g, "\n");
  if (!normalized.trim()) return [];
  return normalized
    .split(/[,;\n]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/** Display current `in` filter values in the textarea */
export function stringifyInListForInput(items: string[]): string {
  return items.join("\n");
}

/**
 * Build API `value` for `in`: strings for text/bool-like fields, numbers when
 * field type is int/float and every token parses as a finite number.
 */
export function normalizeInValueForApi(
  items: string[],
  dataType?: DataType,
): string[] | number[] {
  if (items.length === 0) return [];
  const trimmed = items.map((s) => s.trim()).filter((s) => s.length > 0);
  if (dataType !== "int" && dataType !== "float") {
    return trimmed;
  }
  const nums = trimmed.map((s) => Number(s));
  const allNumeric =
    trimmed.length > 0 &&
    nums.every((n) => !Number.isNaN(n) && Number.isFinite(n));
  return allNumeric ? nums : trimmed;
}
