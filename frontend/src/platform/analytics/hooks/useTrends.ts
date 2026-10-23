import { type UseQueryResult } from "@tanstack/react-query";

import { useAnalyticsScope } from "../AnalyticsScope";
import type { AnalyticsWindow, TrendMetric, TrendsResponse } from "../types";
import { useAnalyticsQuery } from "./useAnalyticsQuery";

export function useTrends(
  agentId: string | null | undefined,
  window: AnalyticsWindow,
  opts: { bucket?: "day" | "week" | "month"; metrics?: TrendMetric[] } = {},
): UseQueryResult<TrendsResponse> {
  const bucket = opts.bucket ?? "day";
  const metrics = opts.metrics ?? [
    "success_rate",
    "avg_ms",
    "cost",
    "failures",
  ];
  const { apis, adapt } = useAnalyticsScope();
  return useAnalyticsQuery<TrendsResponse>({
    resource: "trends",
    endpoint: apis.TRENDS,
    transform: adapt.trends,
    agentId,
    window,
    params: { bucket, metrics: metrics.join(",") },
    keyExtra: [bucket, metrics.join(",")],
    // An agent with no analytics has no series — which every chart already
    // renders as its "No data for this period" state. Reporting it as a load
    // failure instead was wrong twice over: nothing failed, and there is
    // nothing to retry.
    emptyOn404: () => ({
      agent_id: agentId ?? "",
      bucket,
      buckets: 0,
      since: "",
      until: "",
      metrics,
      series: [],
      regression: { detected: false },
    }),
  });
}
