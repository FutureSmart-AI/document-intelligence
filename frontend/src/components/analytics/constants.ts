import type { AnalyticsTabKey } from "@/platform/analytics/types";

/**
 * The agent dashboard's five tabs, plus one.
 *
 * Deliberately not widening `AnalyticsTabKey` itself: that union drives the
 * dashboard page's exhaustive `tabComponents` record, and adding a key there
 * would oblige a product with no `/failures` read to render a tab it cannot
 * fill.
 */
export type DiTabKey = AnalyticsTabKey | "models";

/**
 * The artifact's six tabs, in its order.
 *
 * Model performance sits after Cost because it is the same numbers cut a
 * different way — per answer model rather than per window — and Question
 * history stays last as the raw drill-down it is.
 */
export const DI_ANALYTICS_TABS: { key: DiTabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "quality", label: "Quality & accuracy" },
  { key: "performance", label: "Performance" },
  { key: "cost", label: "Cost" },
  { key: "models", label: "Model performance" },
  // "Question history", not "Query Logs": this product's readers ask questions
  // of their documents, and the artifact names the tab after what it lists.
  { key: "querylogs", label: "Question history" },
];

/** Just the keys, for the window hook that validates `?tab=`. */
export const DI_ANALYTICS_TAB_KEYS = DI_ANALYTICS_TABS.map((tab) => tab.key);

/**
 * Bar colours for the model comparisons, as this product's validated series
 * tokens.
 *
 * Read through `var()` so each theme mode gets its own step, and deliberately
 * not the status colours: these bars rank models, and one painted in the danger
 * red would read as a failure rather than as the slowest of four.
 */
export const COMPARE_BAR_SERIES = [
  "var(--di-series-1)",
  "var(--di-series-2)",
  "var(--di-series-3)",
  "var(--di-series-4)",
];
