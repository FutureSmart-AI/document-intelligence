import dayjs from "dayjs";

import type { TableColumn } from "@/components/knowledge/table/types";
import type { DocumentSection } from "@/data/documentDetail";

/**
 * Where a field's value appears in the text the extraction read.
 *
 * The knowledge service keeps no per-field source span — chunk metadata is
 * empty for collections extracted at document level (live-checked 2026-09-24,
 * strategy `deterministic_chunking_doc_metadata_only`) — so this is a text
 * match, not the model's own account of where it looked. That is still the
 * check a reader wants: does the value say what the document says. A value the
 * extraction normalised ("GBP" for "£", an invented summary) has nothing to
 * match, and gets no control rather than a wrong one.
 */
export interface TextMatch {
  sectionId: string;
  start: number;
  end: number;
}

const DATE_FORMATS = [
  "DD MMMM YYYY",
  "D MMMM YYYY",
  "MMMM D, YYYY",
  "DD MMM YYYY",
  "D MMM YYYY",
  "MMM D, YYYY",
  "DD/MM/YYYY",
  "MM/DD/YYYY",
  "YYYY-MM-DD",
];

/** Shorter than this, a text value matches inside too many other words. */
const MIN_TEXT_LENGTH = 2;

/** The spellings of a value worth looking for, most literal first. */
function candidates(column: TableColumn, value: unknown): string[] {
  if (value == null || typeof value === "boolean" || Array.isArray(value))
    return [];
  if (typeof value === "object") return [];

  if (typeof value === "number") {
    if (!Number.isFinite(value)) return [];
    const spelled = new Set<string>([
      String(value),
      value.toLocaleString("en-US"),
      value.toLocaleString("en-US", { minimumFractionDigits: 2 }),
    ]);
    if (Number.isInteger(value)) spelled.add(value.toFixed(2));
    return [...spelled];
  }

  const text = String(value).trim();
  if (text.length < MIN_TEXT_LENGTH) return [];
  if (column.vtype === "date") {
    const parsed = dayjs(text);
    if (!parsed.isValid()) return [text];
    return [text, ...DATE_FORMATS.map((format) => parsed.format(format))];
  }
  return [text];
}

const isDigit = (char: string | undefined) => !!char && /\d/.test(char);

/**
 * True when the match at `start` is the whole number, not a piece of a longer
 * one — "18" must not be found in "0.18" or "2018", nor "7 March" in "17 March".
 */
function standsAlone(text: string, start: number, end: number): boolean {
  const before = text[start - 1];
  const after = text[end];
  if (isDigit(before) || isDigit(after)) return false;
  if (before === "." && isDigit(text[start - 2])) return false;
  if ((after === "." || after === ",") && isDigit(text[end + 1])) return false;
  return true;
}

/** The first place the value appears, or `null` when it appears nowhere. */
export function locateValue(
  sections: DocumentSection[],
  column: TableColumn,
  value: unknown,
): TextMatch | null {
  // Numbers and dates must not match inside a longer run of digits.
  const bounded = typeof value === "number" || column.vtype === "date";
  for (const needle of candidates(column, value)) {
    const lowered = needle.toLowerCase();
    for (const section of sections) {
      const haystack = section.text.toLowerCase();
      let from = 0;
      for (;;) {
        const start = haystack.indexOf(lowered, from);
        if (start < 0) break;
        const end = start + lowered.length;
        if (!bounded || standsAlone(haystack, start, end))
          return { sectionId: section.id, start, end };
        from = start + 1;
      }
    }
  }
  return null;
}
