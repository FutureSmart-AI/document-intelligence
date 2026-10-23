import { type UseQueryResult } from "@tanstack/react-query";

import { useAnalyticsScope } from "../AnalyticsScope";
import type { AnalyticsWindow, Scorecard } from "../types";
import { useAnalyticsQuery } from "./useAnalyticsQuery";

export function useScorecard(
  agentId: string | null | undefined,
  window: AnalyticsWindow,
  opts: {
    /** Overrides the scope's `top_n`; the scope's own value is the default. */
    topN?: number;
    /**
     * `usage.per_day` granularity. Omitted from the request when undefined, so
     * a backend that publishes no bucket vocabulary is never sent one — every
     * analytics parameter is whitelisted server-side.
     */
    bucket?: string;
  } = {},
): UseQueryResult<Scorecard> {
  const { apis, adapt, limits } = useAnalyticsScope();
  const topN = opts.topN ?? limits.scorecardTopN;
  return useAnalyticsQuery<Scorecard>({
    resource: "scorecard",
    endpoint: apis.SCORECARD,
    transform: adapt.scorecard,
    agentId,
    window,
    params: { top_n: topN, ...(opts.bucket ? { bucket: opts.bucket } : {}) },
    keyExtra: [topN, opts.bucket ?? ""],
  });
}
