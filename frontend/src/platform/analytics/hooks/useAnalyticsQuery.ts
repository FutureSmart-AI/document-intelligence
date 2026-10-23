import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { useAnalyticsScope, type AnalyticsResource } from "../AnalyticsScope";
import { windowKey, windowToRequestParams } from "../constants";
import type { AnalyticsWindow } from "../types";
import { getHttpStatus } from "../utils";

export function useAnalyticsQuery<T>(args: {
  resource: AnalyticsResource;
  endpoint: string;
  agentId: string | null | undefined;
  window: AnalyticsWindow;
  params?: Record<string, unknown>;
  keyExtra?: ReadonlyArray<unknown>;
  /**
   * Post-processes the response before it reaches the caller. Deliberately not
   * part of the query key — it reshapes what one consumer sees, so two consumers
   * with different scopes still share the one cached request.
   */
  select?: (data: T) => T;
  /**
   * Normalizes the raw body into `T` before it is cached, for a backend that
   * answers the same information in a different shape (see `AnalyticsScope`).
   * Unlike `select` this runs once per fetch and everything downstream — the
   * `select` above included — sees only the normalized result.
   */
  transform?: (raw: unknown) => T;
  /**
   * What this resource looks like when the agent has no analytics yet.
   *
   * A `404` here means "this agent has never produced a query", which is the
   * documented first-run state — and the state every agent that predates
   * analytics is in, since there is no historical backfill. Without this the
   * charts render "Failed to load. Try again." over a perfectly healthy agent
   * and invite a reader to retry something that will 404 forever.
   */
  emptyOn404?: () => T;
}): UseQueryResult<T> {
  const {
    resource,
    endpoint,
    agentId,
    window,
    params = {},
    keyExtra = [],
    select,
    transform,
    emptyOn404,
  } = args;
  // How this backend wants the window expressed. Part of the query key via
  // `windowKey` already — the builder is a property of the scope, not of the
  // window, and a scope never changes under a mounted page.
  const { windowParams } = useAnalyticsScope();
  const toWindowParams = windowParams[resource] ?? windowToRequestParams;
  return useQuery<T>({
    queryKey: ["analytics", resource, agentId, windowKey(window), ...keyExtra],
    enabled: Boolean(agentId),
    select,
    // The client default retries twice, which for analytics means three
    // requests per endpoint before an empty screen settles. None of these
    // statuses change on a retry: 404 is "no data yet", 401/403 is the wrong
    // credentials or agent, 422 is a parameter this build will keep sending.
    retry: (failureCount, error) => {
      const status = getHttpStatus(error);
      if (status && status >= 400 && status < 500) return false;
      return failureCount < 2;
    },
    queryFn: async () => {
      // buildApiUrl infers its required params from the literal template type;
      // `endpoint: string` widens that to `never`, so we cast to the shared
      // "{agent_id}" shape. Sound because every APIS.* template has exactly one
      // {agent_id} param; revisit if an endpoint ever takes different params.
      const url = buildApiUrl(endpoint as "{agent_id}", {
        agent_id: agentId as string,
      });
      try {
        const res = await axios.get(url, {
          params: { ...toWindowParams(window), ...params },
        });
        return transform ? transform(res.data) : (res.data as T);
      } catch (error) {
        if (emptyOn404 && getHttpStatus(error) === 404) return emptyOn404();
        throw error;
      }
    },
  });
}
