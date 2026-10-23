// Minimal RFC-4180 CSV parser — the inverse of rowsToCsv. Handles quoted
// fields, escaped quotes (""), and commas / newlines inside quotes.

const coerce = (raw: string, wasQuoted: boolean): unknown => {
  if (wasQuoted) return raw;
  if (raw === "") return "";
  // Only treat as a number when the token round-trips exactly (rejects
  // leading-zero ids like "007", phone numbers, etc.).
  const n = Number(raw);
  if (!Number.isNaN(n) && String(n) === raw) return n;
  return raw;
};

/** Tokenize CSV text into a grid of cells, tracking which cells were quoted. */
const tokenize = (text: string): { value: string; quoted: boolean }[][] => {
  const rows: { value: string; quoted: boolean }[][] = [];
  let row: { value: string; quoted: boolean }[] = [];
  let field = "";
  let quoted = false; // currently inside a quoted field
  let fieldWasQuoted = false; // this field contained a quoted segment
  let i = 0;

  const pushField = () => {
    row.push({ value: field, quoted: fieldWasQuoted });
    field = "";
    fieldWasQuoted = false;
  };
  const pushRow = () => {
    pushField();
    rows.push(row);
    row = [];
  };

  while (i < text.length) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        quoted = false;
        i += 1;
        continue;
      }
      field += ch;
      i += 1;
      continue;
    }
    if (ch === '"') {
      quoted = true;
      fieldWasQuoted = true;
      i += 1;
      continue;
    }
    if (ch === ",") {
      pushField();
      i += 1;
      continue;
    }
    if (ch === "\r") {
      // swallow CRLF as a single line break
      if (text[i + 1] === "\n") i += 1;
      pushRow();
      i += 1;
      continue;
    }
    if (ch === "\n") {
      pushRow();
      i += 1;
      continue;
    }
    field += ch;
    i += 1;
  }
  // flush the final field/row unless the file ended on a clean line break
  if (field !== "" || fieldWasQuoted || row.length > 0) pushRow();
  return rows;
};

export const csvToRows = (text: string): Record<string, unknown>[] => {
  const grid = tokenize(text ?? "");
  if (grid.length < 2) return [];
  const headers = grid[0].map((c) => c.value);
  return grid.slice(1).map((cells) => {
    const obj: Record<string, unknown> = {};
    headers.forEach((key, idx) => {
      const cell = cells[idx];
      obj[key] = cell ? coerce(cell.value, cell.quoted) : "";
    });
    return obj;
  });
};
