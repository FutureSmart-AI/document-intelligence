import { isPlainObject } from "lodash-es";

/**
 * The rows behind a charted answer.
 *
 * A numeric question is answered by running a chart node over the collection's
 * metadata dataframe, and the node returns that frame alongside the plot:
 *
 *     "chart_output": {
 *       "chart_data": { "data": [{ "label": …, "value": … }], … },
 *       "dataframe": { "columns": ["name", "years"],
 *                      "data": [["Ada", 8], ["Grace", 12]],
 *                      "shape": [2, 2], "dtypes": {…} }
 *     }
 *
 * `chart_data` is what the plot is drawn from and carries only the two series
 * columns; `dataframe` is the whole result, which is what the reader wants to
 * filter, sort and export. Both row orientations pandas emits are read — a list
 * of value arrays (`orient="split"`) and a list of records — because which one
 * arrives depends on how the node serialised it.
 */
export interface ChartDataframe {
  columns: string[];
  rows: Record<string, unknown>[];
  /** Rows the frame reported holding, when its `shape` said so. */
  totalRows?: number;
}

const asObject = (value: unknown): Record<string, unknown> | null => {
  if (isPlainObject(value)) return value as Record<string, unknown>;
  if (typeof value === "string" && value.trim().startsWith("{")) {
    try {
      const parsed = JSON.parse(value);
      return isPlainObject(parsed) ? (parsed as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  }
  return null;
};

/** The frame an assistant message carries, or null when it carries none. */
export function readChartDataframe(message: unknown): ChartDataframe | null {
  const knowledge = asObject(asObject(message)?.data)?.knowledge;
  const chart = asObject(asObject(knowledge)?.chart_output);
  const frame = asObject(chart?.dataframe);
  if (!frame) return null;

  const data = frame.data;
  if (!Array.isArray(data) || !data.length) return null;

  const declared = Array.isArray(frame.columns)
    ? frame.columns.map((column) => String(column))
    : [];

  // Records: the keys are the columns, and `columns` — when the node sent it —
  // fixes their order.
  if (isPlainObject(data[0])) {
    const seen = new Set<string>();
    for (const row of data) {
      if (!isPlainObject(row)) continue;
      for (const key of Object.keys(row as object)) seen.add(key);
    }
    const columns = declared.length
      ? [
          ...declared.filter((column) => seen.has(column)),
          ...[...seen].filter((column) => !declared.includes(column)),
        ]
      : [...seen];
    return {
      columns,
      rows: data.filter(isPlainObject) as Record<string, unknown>[],
      totalRows: Array.isArray(frame.shape)
        ? Number(frame.shape[0]) || undefined
        : undefined,
    };
  }

  // Value arrays: unusable without the column names, since a row would have no
  // keys to show it under.
  if (!declared.length) return null;
  const rows = data
    .filter((row): row is unknown[] => Array.isArray(row))
    .map((row) =>
      Object.fromEntries(declared.map((column, index) => [column, row[index]])),
    );
  if (!rows.length) return null;

  return {
    columns: declared,
    rows,
    totalRows: Array.isArray(frame.shape)
      ? Number(frame.shape[0]) || undefined
      : undefined,
  };
}
