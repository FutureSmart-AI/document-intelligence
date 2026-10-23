import type { QueryGroup } from "@/data/reports";

/**
 * Reads an uploaded question list into the groups a report is made of.
 *
 * Parsed **in the browser**, not posted: the reports API takes a report whole,
 * with its groups already formed, so a file has to become that shape before
 * anything is sent. Doing it here also means a malformed file is reported as a
 * malformed file rather than as a 422 from a save.
 *
 * Both shapes the format panel documents are accepted:
 *
 * - **JSON** — `{ "query_groups": [{ "type", "queries": [] }] }`, and the
 *   longer `{ "question_groups": [{ "group_type", "questions": [{
 *   "question_text" }] }] }` the artifact's sample uses, since a reader
 *   following either one is following the documentation they were shown.
 * - **CSV** — `group_id, group_name, group_type, question_id, question_text`.
 *   Rows are grouped by `group_id` in first-seen order; `group_name` is read
 *   and dropped, because a group on this service has a type and its questions
 *   and no name.
 *
 * Anything else throws with a message the form shows as-is.
 */

type Row = Record<string, string>;

const GROUP_TYPES = new Set(["independent", "sequential"]);

const asType = (value: unknown): QueryGroup["type"] => {
  const text = String(value ?? "")
    .trim()
    .toLowerCase();
  return GROUP_TYPES.has(text) ? (text as QueryGroup["type"]) : "independent";
};

/** One CSV line, honouring double quotes and escaped `""`. */
function splitCsvLine(line: string): string[] {
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (quoted) {
      if (char === '"') {
        if (line[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        cell += char;
      }
      continue;
    }
    if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      cells.push(cell);
      cell = "";
    } else {
      cell += char;
    }
  }
  cells.push(cell);
  return cells.map((value) => value.trim());
}

function parseCsv(text: string): QueryGroup[] {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length < 2) {
    throw new Error(
      "That CSV has no rows under its header. Add one row per question.",
    );
  }

  const header = splitCsvLine(lines[0]).map((cell) => cell.toLowerCase());
  const questionColumn = header.findIndex((cell) =>
    ["question_text", "query_text", "question", "query"].includes(cell),
  );
  if (questionColumn === -1) {
    throw new Error(
      "That CSV has no question column. Expected a `question_text` column.",
    );
  }

  const rows: Row[] = lines.slice(1).map((line) => {
    const cells = splitCsvLine(line);
    return Object.fromEntries(
      header.map((name, index) => [name, cells[index] ?? ""]),
    );
  });

  const order: string[] = [];
  const groups = new Map<string, QueryGroup>();
  for (const row of rows) {
    const question = row[header[questionColumn]];
    if (!question) continue;
    const key = row.group_id || row.group || "g1";
    if (!groups.has(key)) {
      order.push(key);
      groups.set(key, {
        type: asType(row.group_type || row.type),
        queries: [],
      });
    }
    groups.get(key)?.queries.push(question);
  }

  const parsed = order
    .map((key) => groups.get(key))
    .filter((group): group is QueryGroup => Boolean(group?.queries.length));
  if (!parsed.length) {
    throw new Error("That CSV holds no questions.");
  }
  return parsed;
}

function parseJson(text: string): QueryGroup[] {
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new Error("That file is not valid JSON.");
  }

  const record = (body ?? {}) as Record<string, unknown>;
  const raw = record.query_groups ?? record.question_groups ?? body;
  if (!Array.isArray(raw)) {
    throw new Error(
      "Expected a `query_groups` array of groups, each with its questions.",
    );
  }

  const parsed = raw
    .map((entry) => {
      const group = (entry ?? {}) as Record<string, unknown>;
      const questions = group.queries ?? group.questions ?? [];
      const queries = (Array.isArray(questions) ? questions : [])
        .map((question) =>
          typeof question === "string"
            ? question
            : String(
                (question as Record<string, unknown>)?.question_text ??
                  (question as Record<string, unknown>)?.query_text ??
                  (question as Record<string, unknown>)?.text ??
                  "",
              ),
        )
        .map((question) => question.trim())
        .filter(Boolean);
      return {
        type: asType(group.type ?? group.group_type),
        queries,
      };
    })
    .filter((group) => group.queries.length);

  if (!parsed.length) {
    throw new Error("That file holds no questions.");
  }
  return parsed;
}

export function parseQuestionGroups(
  text: string,
  fileName: string,
): QueryGroup[] {
  const body = text.trim();
  if (!body) throw new Error("That file is empty.");
  // Chosen by content, not only by extension: a `.txt` export of either format
  // still parses, and a `.csv` holding JSON is read as the JSON it is.
  const looksJson = body.startsWith("{") || body.startsWith("[");
  return looksJson || /\.json$/i.test(fileName)
    ? parseJson(body)
    : parseCsv(body);
}
