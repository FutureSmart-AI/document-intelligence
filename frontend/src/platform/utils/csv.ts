/**
 * CSV serialization shared by every table export in the dashboard
 * (Data View, Knowledge list, NL2SQL results).
 *
 * Rules:
 * - Cells are quoted only when they contain a delimiter, quote or newline.
 * - Arrays of scalars become `a; b` so spreadsheets stay readable; nested
 *   objects / arrays of objects fall back to JSON.
 * - Cells starting with `=`, `+`, `@`, tab or CR are prefixed with `'` so a
 *   spreadsheet does not evaluate exported knowledge text as a formula.
 */

/** Column descriptor: `key` reads the row, `label` is the header cell. */
export interface CsvColumn {
  key: string;
  label?: string;
}

const FORMULA_TRIGGERS = /^[=+@\t\r]/;

const stringifyValue = (value: unknown): string => {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) {
    const allScalar = value.every(
      (item) => item === null || typeof item !== "object",
    );
    return allScalar
      ? value.map((item) => (item === null ? "" : String(item))).join("; ")
      : JSON.stringify(value);
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

/** Serializes a single value into a CSV-safe cell. */
export const csvCell = (value: unknown): string => {
  let str = stringifyValue(value);
  if (FORMULA_TRIGGERS.test(str)) str = `'${str}`;
  if (/[",\n\r]/.test(str)) return `"${str.replace(/"/g, '""')}"`;
  return str;
};

const normalizeColumns = (
  rows: Record<string, unknown>[],
  columns?: Array<string | CsvColumn>,
): CsvColumn[] => {
  if (columns && columns.length > 0) {
    return columns.map((col) => (typeof col === "string" ? { key: col } : col));
  }
  return Object.keys(rows[0] ?? {}).map((key) => ({ key }));
};

/**
 * Builds a CSV document. Without `columns` the keys of the first row are used,
 * which keeps the original NL2SQL behaviour; pass `columns` to control order
 * and header labels (Data View / Knowledge tables do).
 */
export const rowsToCsv = (
  rows: Record<string, unknown>[],
  columns?: Array<string | CsvColumn>,
): string => {
  const cols = normalizeColumns(rows ?? [], columns);
  if (cols.length === 0) return "";
  if ((!rows || rows.length === 0) && !columns) return "";

  const headerLine = cols.map((col) => csvCell(col.label ?? col.key)).join(",");
  const body = (rows ?? []).map((row) =>
    cols.map((col) => csvCell(row?.[col.key])).join(","),
  );
  return [headerLine, ...body].join("\r\n");
};

/**
 * Triggers a browser download for an already-built CSV string. A UTF-8 BOM is
 * prepended so Excel renders non-ASCII knowledge text correctly.
 */
export const downloadCsvFile = (csv: string, filename: string): void => {
  const BOM = "\uFEFF";
  const blob = new Blob([BOM + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  // A CSV saved without the extension will not open in a spreadsheet app, so the
  // suffix is enforced here rather than trusted to every call site.
  link.download = /\.csv$/i.test(filename.trim())
    ? filename.trim()
    : `${filename.trim().replace(/\.$/, "") || "export"}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};

/** Serializes rows and downloads them in one step. */
export const downloadRowsAsCsv = (
  rows: Record<string, unknown>[],
  filename: string,
  columns?: Array<string | CsvColumn>,
): void => {
  downloadCsvFile(rowsToCsv(rows, columns), filename);
};

/**
 * `prefix-YYYYMMDD-HHmm.csv` — keeps repeated exports from overwriting each
 * other in the downloads folder.
 */
export const csvFilename = (
  prefix: string,
  date: Date = new Date(),
): string => {
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}`;
  return `${prefix}-${stamp}.csv`;
};
