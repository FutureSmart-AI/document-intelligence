import { cellText, toColumnLabel } from "@/components/knowledge/table/format";
import type { TableColumn } from "@/components/knowledge/table/types";

/**
 * Sixty fields off one résumé are not sixty unrelated facts.
 *
 * The service flattens repeated objects into `education_0_degree`,
 * `education_0_institution`, `experience_1_company` — so a reader scanning the
 * panel reads the word "Education" eight times before reaching the value they
 * came for. These helpers put that prefix back where it belongs: once, as a
 * heading over its own rows, leaving each row to say only what it adds.
 */

/** One row of the panel: the column, and the label it shows inside its group. */
export interface FieldEntry {
  column: TableColumn;
  /** `Degree` inside `Education #1`; the column's own label when ungrouped. */
  label: string;
}

/** A run of fields that came out of one repeated object, or a loose field. */
export interface FieldGroup {
  /** `education_0`, or `""` for fields that belong to no group. */
  key: string;
  /** `Education #1`, or `null` when the fields stand on their own. */
  label: string | null;
  entries: FieldEntry[];
}

/** `education_0_degree` → prefix `education_0`, remainder `degree`. */
function splitIndexed(key: string): { groupKey: string; rest: string } | null {
  const match = /^(.*?[_.]\d+)[_.](.+)$/.exec(key);
  return match ? { groupKey: match[1], rest: match[2] } : null;
}

/**
 * The panel's rows, in the order the columns arrive, with indexed runs folded
 * into a group at the position their first member held.
 *
 * A prefix seen only once is left ungrouped: a heading over a single row costs
 * a line and saves nothing, and `summary_text` is not an object with one field.
 */
export function groupFields(columns: TableColumn[]): FieldGroup[] {
  const counts = new Map<string, number>();
  for (const column of columns) {
    const split = splitIndexed(column.key);
    if (split)
      counts.set(split.groupKey, (counts.get(split.groupKey) ?? 0) + 1);
  }

  const groups: FieldGroup[] = [];
  const byKey = new Map<string, FieldGroup>();

  for (const column of columns) {
    const split = splitIndexed(column.key);
    const grouped = split && (counts.get(split.groupKey) ?? 0) > 1;
    if (!grouped) {
      groups.push({
        key: "",
        label: null,
        entries: [{ column, label: column.label }],
      });
      continue;
    }
    let group = byKey.get(split.groupKey);
    if (!group) {
      group = {
        key: split.groupKey,
        label: toColumnLabel(split.groupKey),
        entries: [],
      };
      byKey.set(split.groupKey, group);
      groups.push(group);
    }
    group.entries.push({ column, label: toColumnLabel(split.rest) });
  }

  return groups;
}

/**
 * Does this field answer the search box?
 *
 * Both the label and the value are searched, because either is a reason to
 * type: "degree" finds the field, "Mumbai University" finds the row that says
 * it without the reader having to remember which field held it.
 */
export function matchesQuery(
  entry: FieldEntry,
  groupLabel: string | null,
  value: unknown,
  query: string,
): boolean {
  if (!query) return true;
  const needle = query.toLowerCase();
  return (
    entry.column.label.toLowerCase().includes(needle) ||
    entry.label.toLowerCase().includes(needle) ||
    (groupLabel?.toLowerCase().includes(needle) ?? false) ||
    cellText(entry.column, value).toLowerCase().includes(needle)
  );
}
