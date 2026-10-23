import { isPlainObject } from "lodash-es";

import type { ChartData } from "@/platform/chat-core";

/**
 * The chart a knowledge-service answer can carry.
 *
 * Document Intelligence answers a numeric/aggregate question by running a chart
 * node over the metadata dataframe, and returns its result on the turn's
 * evidence block — `data.knowledge.chart_output` — rather than as the chat
 * service's `data.nl2sql` or the NL2SQL stream's top-level `chart_output`.
 * Measured against the live service (2026-08-25):
 *
 *     "chart_output": {
 *       "type": "chart_response",
 *       "chart_suggestion": "Pie Chart",
 *       "chart_data": { "data": [{ "label": …, "value": … }], "metadata": {…} },
 *       "dataframe": { "columns": […], "data": […], "shape": […], "dtypes": {…} },
 *       "chart_generated": true
 *     }
 *
 * and, when the node decides the data cannot be plotted:
 *
 *     { "type": "direct_response", "message": "…", "reasoning": null,
 *       "graph_generated": false }
 *
 * `chart_data` is already exactly `ChatChart`'s props (`{ data, metadata }` with
 * `label`/`value` records), so the whole mapping is finding it and refusing the
 * `direct_response` variant — whose `message` restates the answer text that is
 * rendered above it anyway.
 */
export interface KnowledgeChartView {
  chartData: ChartData;
  chartSuggestion: string;
}

/**
 * A stored thread returns `data` as an object, but the dashboard's session route
 * hands some services' payloads back as JSON strings — parse one rather than
 * silently dropping the chart.
 */
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

export const normalizeKnowledgeChart = (
  message: unknown,
): KnowledgeChartView | null => {
  const knowledge = asObject(asObject(message)?.data)?.knowledge;
  const chart = asObject(asObject(knowledge)?.chart_output);
  if (!chart) return null;

  // The node ran but produced prose, not a plot.
  if (chart.chart_generated === false || chart.graph_generated === false) {
    return null;
  }
  if (chart.type === "direct_response") return null;

  const chartData = asObject(chart.chart_data);
  if (!chartData || chartData.data == null) return null;

  return {
    chartData: chartData as unknown as ChartData,
    // Without a suggestion `ChatChart` falls through to a scatter plot, which is
    // the wrong shape for the categorical `{label, value}` rows this node emits.
    chartSuggestion:
      typeof chart.chart_suggestion === "string" && chart.chart_suggestion
        ? chart.chart_suggestion
        : "Bar Chart",
  };
};
