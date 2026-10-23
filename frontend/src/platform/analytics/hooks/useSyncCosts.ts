import { useMutation, type UseMutationResult } from "@tanstack/react-query";

import { queryClient } from "@/platform/components/ReactQueryClientProvider";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { useAnalyticsScope } from "../AnalyticsScope";
import { windowToRequestParams } from "../constants";
import type { AnalyticsWindow } from "../types";

/**
 * The "Refresh costs" backstop.
 *
 * `strategy` is passed per call rather than fixed here: which reconciliation
 * the backend should run is the caller's choice among the values its scope
 * publishes, and a backend that declares no strategies is sent none — an
 * analytics parameter an endpoint has not whitelisted is a 422.
 */
export function useSyncCosts(
  agentId: string | null | undefined,
  window: AnalyticsWindow,
): UseMutationResult<unknown, Error, { strategy?: string } | void> {
  const { apis } = useAnalyticsScope();
  return useMutation<unknown, Error, { strategy?: string } | void>({
    mutationFn: async (vars) => {
      const url = buildApiUrl(apis.SYNC_COSTS as "{agent_id}", {
        agent_id: agentId as string,
      });
      const strategy = vars && "strategy" in vars ? vars.strategy : undefined;
      const res = await axios.post(url, null, {
        params: {
          ...windowToRequestParams(window),
          ...(strategy ? { strategy } : {}),
        },
      });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["analytics", "scorecard", agentId],
      });
      queryClient.invalidateQueries({
        queryKey: ["analytics", "queries", agentId],
      });
    },
  });
}
