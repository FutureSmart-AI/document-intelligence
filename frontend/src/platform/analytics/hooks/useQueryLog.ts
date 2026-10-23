import { useMemo } from "react";

import { type UseQueryResult } from "@tanstack/react-query";

import { useAnalyticsScope } from "../AnalyticsScope";
import { stripHiddenServices } from "../lib/hiddenServices";
import type {
  AnalyticsWindow,
  QueryLogFilters,
  QueryLogResponse,
} from "../types";
import { useAnalyticsQuery } from "./useAnalyticsQuery";

export function useQueryLog(
  agentId: string | null | undefined,
  window: AnalyticsWindow,
  opts: {
    sort?: string;
    order?: "asc" | "desc";
    filters?: QueryLogFilters;
    limit?: number;
    offset?: number;
  } = {},
): UseQueryResult<QueryLogResponse> {
  const {
    sort = "latency_ms",
    order = "desc",
    filters = {},
    limit = 20,
    offset = 0,
  } = opts;
  // Every query-derived surface — the log table, the slowest/most-expensive
  // tables, the per-node breakdown, the row's node trace — is built from these
  // rows, so scoping them here scopes all of it at once.
  const { hiddenServices, apis, adapt } = useAnalyticsScope();
  const select = useMemo(
    () =>
      hiddenServices.length
        ? (data: QueryLogResponse) => stripHiddenServices(data, hiddenServices)
        : undefined,
    [hiddenServices],
  );
  return useAnalyticsQuery<QueryLogResponse>({
    select,
    resource: "queries",
    endpoint: apis.QUERIES,
    transform: adapt.queries,
    agentId,
    window,
    params: { sort, order, ...filters, limit, offset },
    keyExtra: [sort, order, JSON.stringify(filters), limit, offset],
    // No queries recorded is an empty log, not a broken one — and it is what
    // the tables, the per-node breakdown and every chart derived from this
    // sample already know how to draw.
    emptyOn404: () => ({
      agent_id: agentId ?? "",
      period: "",
      sort,
      order,
      total_matching: 0,
      limit,
      offset,
      returned: 0,
      queries: [],
    }),
  });
}
