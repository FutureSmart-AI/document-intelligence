import { get, isArray, isNil, isPlainObject } from "lodash-es";

export interface Nl2sqlChartView {
  suggestion?: string;
  data?: unknown;
  metadata?: unknown;
}

/** One column of the result, as the service declared it. */
export interface Nl2sqlColumn {
  name: string;
  /** The type the service reported for the column, when it reported one. */
  type?: string;
}

export interface Nl2sqlView {
  sqlQuery?: string;
  tablesUsed: string[];
  executionTimeMs?: number;
  rowCount?: number;
  rowCountIsPreview: boolean;
  previewRows?: Record<string, unknown>[];
  /**
   * The declared columns, in the order and with the types the service sent.
   *
   * The rows alone cannot say either: key order on a JSON object is the
   * service's insertion order at best, and every value's type is guessed from
   * what happens to be in the column. `db_result.columns` states both.
   */
  columns?: Nl2sqlColumn[];
  chart?: Nl2sqlChartView;
  resultS3Url?: string;
}

const toChartView = (
  raw: Record<string, unknown>,
): Nl2sqlChartView | undefined => {
  const meta = raw.chart_metadata;
  if (isPlainObject(meta)) {
    const m = meta as Record<string, unknown>;
    return {
      suggestion: typeof m.suggestion === "string" ? m.suggestion : undefined,
      data: m.data,
      metadata: m.metadata,
    };
  }
  const suggestion =
    typeof raw.chart_suggestion === "string" ? raw.chart_suggestion : undefined;
  const chartData = raw.chart_data as Record<string, unknown> | undefined;
  if (!suggestion && !chartData) return undefined;
  return { suggestion, data: chartData?.data, metadata: chartData?.metadata };
};

/**
 * Rows for a turn whose only tabular data is the chart's own points.
 *
 * The pairs arrive spelled two ways: `{ x, y }` from the documented block and
 * `{ category, value }` from the NL2SQL service's `graph_output.chart_data`.
 * Reading only the first spelling built a row per point with every cell
 * `undefined`, so the table drew the right headers and the right row count over
 * blank cells.
 */
const deriveRowsFromChart = (
  chart?: Nl2sqlChartView,
): Record<string, unknown>[] | undefined => {
  if (!chart || !isArray(chart.data)) return undefined;
  const xLabel = (get(chart, ["metadata", "x_axis", "label"]) as string) || "x";
  const yLabel = (get(chart, ["metadata", "y_axis", "label"]) as string) || "y";
  const rows = (chart.data as Array<Record<string, unknown>>)
    .filter(isPlainObject)
    .flatMap((point) => {
      const label = point.x ?? point.category ?? point.label ?? point.index;
      // A stacked point carries its numbers per segment and nothing at the top
      // level, so reading `value` off it gave a row of blank cells. One row per
      // segment, named the way the session view names them.
      const segments = isArray(point.segments)
        ? (point.segments as Array<Record<string, unknown>>).filter(
            isPlainObject,
          )
        : [];
      if (segments.length) {
        return segments.map((segment) => ({
          [xLabel]: `${label ?? ""} · ${segment.category ?? ""}`,
          [yLabel]: segment.value,
        }));
      }
      const value = point.y ?? point.value;
      // Neither half readable: drawing the row anyway is what put the right
      // headers and the right row count over empty cells.
      if (isNil(label) && isNil(value)) return [];
      return [{ [xLabel]: label, [yLabel]: value }];
    });
  return rows.length ? rows : undefined;
};

/**
 * The same block, assembled from a turn that never nested one.
 *
 * The chat service nests the SQL, the tables and the rows under `data.nl2sql`.
 * The NL2SQL service does not: it answers with `debug_info.sql_query`,
 * `debug_info.selected_tables` and `debug_info.db_result`, and puts the chart
 * at the top level as `chart_output` (mirrored as `graph_output`). Reading only
 * the nested spelling is why such a turn drew no sources block at all — there
 * was nothing wrong with the payload, the shape was simply never looked at.
 *
 * `debug_info` is read only as an object: a stored dashboard turn keeps it as a
 * JSON string, and `useChatStream` has already parsed it by the time a message
 * reaches here.
 */
const flatBlock = (message: unknown): Record<string, unknown> | null => {
  const debug = get(message, "debug_info");
  const chart =
    (get(message, "chart_output") as Record<string, unknown> | undefined) ??
    (get(message, "graph_output") as Record<string, unknown> | undefined);
  const block: Record<string, unknown> = {};

  if (isPlainObject(debug)) {
    const d = debug as Record<string, unknown>;
    if (typeof d.sql_query === "string") block.sql_query = d.sql_query;
    if (isArray(d.selected_tables)) block.tables_used = d.selected_tables;
    if (isPlainObject(d.db_result)) block.db_result = d.db_result;
    if (typeof d.selected_db_name === "string")
      block.db_name = d.selected_db_name;
  }
  if (isPlainObject(chart)) {
    const c = chart as Record<string, unknown>;
    if (typeof c.chart_suggestion === "string") {
      block.chart_suggestion = c.chart_suggestion;
    }
    if (isPlainObject(c.chart_data)) block.chart_data = c.chart_data;
  }

  return Object.keys(block).length ? block : null;
};

/** `db_result.columns`, kept in the order and with the types it declared. */
const declaredColumns = (
  dbResult: Record<string, unknown> | undefined,
): Nl2sqlColumn[] | undefined => {
  if (!dbResult || !isArray(dbResult.columns)) return undefined;
  const columns = (dbResult.columns as unknown[]).flatMap((entry) => {
    if (typeof entry === "string") return [{ name: entry }];
    if (!isPlainObject(entry)) return [];
    const column = entry as Record<string, unknown>;
    if (typeof column.name !== "string") return [];
    return [
      {
        name: column.name,
        type: typeof column.type === "string" ? column.type : undefined,
      },
    ];
  });
  return columns.length ? columns : undefined;
};

export const normalizeNl2sql = (message: unknown): Nl2sqlView | null => {
  const raw =
    (get(message, ["data", "nl2sql"]) as Record<string, unknown> | undefined) ??
    (get(message, ["chart_output", "nl2sql"]) as
      Record<string, unknown> | undefined) ??
    // Nothing nested: the NL2SQL service's own flat answer, if this is one.
    (flatBlock(message) as Record<string, unknown> | undefined);
  if (!isPlainObject(raw)) return null;

  const chart = toChartView(raw as Record<string, unknown>);
  const r = raw as Record<string, unknown>;

  // db_result is the agentic_rag shape: { columns, rows, row_count, truncated }
  const dbResult = isPlainObject(r.db_result)
    ? (r.db_result as Record<string, unknown>)
    : undefined;

  const explicitPreview = isArray(r.preview_rows)
    ? (r.preview_rows as Record<string, unknown>[])
    : undefined;
  const dbRows =
    !explicitPreview && dbResult && isArray(dbResult.rows)
      ? (dbResult.rows as unknown[]).filter(
          isPlainObject as (v: unknown) => v is Record<string, unknown>,
        )
      : undefined;
  // Only fall back to chart-derived rows when neither explicit nor db rows exist.
  const derived =
    explicitPreview || dbRows ? undefined : deriveRowsFromChart(chart);
  // Keep every inline row the backend returned — the table paginates client-side,
  // so there's no reason to discard rows here. Slicing to a small preview broke
  // pagination/export when db_result carried the full result set with no S3 export.
  const previewRows = explicitPreview ?? dbRows ?? derived;

  const explicitRowCount =
    typeof r.row_count === "number"
      ? (r.row_count as number)
      : typeof dbResult?.row_count === "number"
        ? (dbResult.row_count as number)
        : undefined;

  // execution_time_ms is the spec field; real agentic_rag responses only carry
  // total latency in debug_info.latency_ms — use it as the header-badge fallback.
  const latencyMs = get(message, ["debug_info", "latency_ms"]);

  return {
    sqlQuery:
      typeof r.sql_query === "string" ? (r.sql_query as string) : undefined,
    tablesUsed: isArray(r.tables_used)
      ? (r.tables_used as unknown[]).filter(
          (x): x is string => typeof x === "string",
        )
      : [],
    executionTimeMs:
      typeof r.execution_time_ms === "number"
        ? (r.execution_time_ms as number)
        : typeof latencyMs === "number"
          ? latencyMs
          : undefined,
    columns: declaredColumns(dbResult),
    rowCount: explicitRowCount ?? previewRows?.length,
    rowCountIsPreview:
      explicitRowCount === undefined && previewRows !== undefined,
    previewRows,
    chart,
    resultS3Url:
      typeof r.result_s3_url === "string"
        ? (r.result_s3_url as string)
        : typeof r.export_url === "string"
          ? (r.export_url as string)
          : undefined,
  };
};
