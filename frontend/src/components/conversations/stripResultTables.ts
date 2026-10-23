/**
 * Drops the markdown table a charted answer wrote when the result card already
 * draws those same rows.
 *
 * A charted answer writes its rows out as a markdown table as well as returning
 * the frame behind them, so an answer with the card under it printed the same
 * result twice — once without the filter, the column picker, the sort, the
 * paging or the export. One table per answer: the card. The prose around it —
 * the heading, the summary, the note — is what the answer is for, and is kept.
 *
 * Only a table that *restates the card* is dropped. An answer often pivots,
 * groups or summarises the frame before writing it out — ten (name, date) rows
 * become one row per name with the dates gathered into a cell — and that table
 * is the answer, not a repeat of it. Dropping it left the reader a heading, a
 * sentence promising a summary "below", and nothing between them.
 */
const SEPARATOR = /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/;

const isRow = (line: string): boolean => line.includes("|");

/** The cells of a table row, with the markdown that styles them removed. */
const cellsOf = (line: string): string[] =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    // A cell is markdown: "**Count**" is the word Count in bold, and carrying
    // the asterisks through would compare "**Count**" against "count".
    .map((cell) => cell.replace(/[*_`]/g, "").trim());

/**
 * A name or value reduced to what two writings of it have in common:
 * `Missing Date`, `missing_date` and `**Missing date**` all compare equal.
 */
const key = (value: unknown): string =>
  String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");

/** The rows a card is drawing, as the set of every value in them. */
const valuesOf = (rows: Record<string, unknown>[]): Set<string> => {
  const values = new Set<string>();
  for (const row of rows)
    for (const value of Object.values(row)) {
      values.add(key(value));
    }
  return values;
};

export interface ResultCardShape {
  /** The columns the card draws, in the order the service declared them. */
  columns: string[];
  /** The rows the card draws. */
  rows: Record<string, unknown>[];
}

/**
 * True when a table is the card written out again rather than a reading of it.
 *
 * Two ways an answer repeats the card: it names the card's columns — all of
 * them or a few, since answers routinely leave an id column out — or, having
 * renamed them on the way out, it fills every one of its cells with a value
 * the card already holds.
 *
 * The second test is what separates a repeat from a summary. An answer that
 * pivots ten (member, date) rows into one row per member writes cells the card
 * has no value for — `2026-09-14, 2026-09-16` is a list the answer built — so
 * it fails, and the table stays. An answer that prints two of the card's three
 * columns back passes on every cell, and goes.
 */
const restatesCard = (
  header: string[],
  body: string[][],
  card: ResultCardShape,
): boolean => {
  if (!card.columns.length || !header.length) return false;
  const columns = new Set(card.columns.map(key));
  if (
    header.length <= card.columns.length &&
    header.every((cell) => columns.has(key(cell)))
  ) {
    return true;
  }
  if (!body.length) return false;
  const values = valuesOf(card.rows);
  return body.every((row) => row.every((cell) => values.has(key(cell))));
};

export function stripResultTables(
  markdown: string,
  card: ResultCardShape = { columns: [], rows: [] },
): string {
  const lines = markdown.split("\n");
  const kept: string[] = [];

  for (let i = 0; i < lines.length; i += 1) {
    const header = lines[i];
    const separator = lines[i + 1];
    const startsTable =
      isRow(header) &&
      separator !== undefined &&
      SEPARATOR.test(separator) &&
      separator.includes("-");

    if (!startsTable) {
      kept.push(header);
      continue;
    }

    // Walk to the end of the block, then decide what to do with it.
    let end = i + 2;
    while (end < lines.length && isRow(lines[end])) end += 1;
    const body = lines.slice(i + 2, end).map(cellsOf);

    if (!restatesCard(cellsOf(header), body, card)) {
      for (let line = i; line < end; line += 1) kept.push(lines[line]);
      i = end - 1;
      continue;
    }

    i = end - 1;

    // Collapse the blank lines the table sat between into one, so the prose
    // above and below does not end up welded together or spread apart.
    while (kept.length && kept[kept.length - 1].trim() === "") kept.pop();
    kept.push("");
  }

  return kept
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
