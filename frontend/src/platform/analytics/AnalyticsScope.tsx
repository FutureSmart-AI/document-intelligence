"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

import {
  APIS,
  DEFAULT_FILTER_OPTIONS,
  DEFAULT_LIMITS,
  type AnalyticsFilterOptions,
  type AnalyticsLimits,
} from "./constants";
import type {
  AnalyticsWindow,
  QueryLogResponse,
  Scorecard,
  TrendsResponse,
} from "./types";

/**
 * `APIS` is `as const`, so its values are literal path types. Widening them to
 * `string` is what lets another product substitute its own routes for the same
 * keys — the shape is "one entry per analytics read", not "these exact paths".
 */
export type AnalyticsApis = { [K in keyof typeof APIS]: string };

/**
 * Reshapes a raw response into the type this page's components expect.
 *
 * Runs inside the query function, before the result is cached, so everything
 * downstream — including `useQueryLog`'s `select` — sees the normalized shape.
 * A product whose backend already answers in the dashboard's shape leaves these
 * unset and pays nothing.
 */
export interface AnalyticsAdapters {
  scorecard?: (raw: unknown) => Scorecard;
  trends?: (raw: unknown) => TrendsResponse;
  queries?: (raw: unknown) => QueryLogResponse;
}

/** Resources a scope can override the request or response shape of. */
export type AnalyticsResource = "scorecard" | "trends" | "queries" | "failures";

/**
 * How a resource expresses the selected time window as request params.
 *
 * Defaults to `windowToRequestParams` — `period`, or `start_date`/`end_date`.
 * Overridable per resource because backends do not agree on how to say
 * "the last 30 days": NL2SQL's `/analytics/trends` takes a `bucket` × `weeks`
 * count rather than a `period`, and every analytics param is whitelisted
 * server-side, so sending the wrong one is a 422 or — worse — a silently
 * different window than the toolbar claims.
 */
export type WindowParamBuilder = (
  window: AnalyticsWindow,
) => Record<string, unknown>;

export interface AnalyticsScopeValue {
  /**
   * Service families this product does not own, lowercase (e.g. `knowledge`).
   *
   * The agent backend reports one agent's analytics whole — a query that used
   * both Knowledge and NL2SQL comes back as a single `multi` row carrying both
   * services' nodes. So a single-product dashboard cannot ask the API for its
   * slice; it declares what is not its own here, and everything derived from a
   * query row — the service chips, the per-node breakdown, the node trace, the
   * service filter — drops those entries.
   *
   * Empty in the agent UI, which owns every service and shows them all.
   */
  hiddenServices: readonly string[];

  /**
   * Where the analytics reads go.
   *
   * The agent dashboard's own routes (`/api/dashboard/v1/agents/{id}/…`) are
   * keyed by chat-registry ids and answer 403 for an `NL2SQL_…` agent, which is
   * why the standalone NL2SQL product cannot share them. It has its own set at
   * `/nl2sql/api/v1/agents/{id}/analytics/…` returning the same shapes, so it
   * overrides the templates here and reuses every tab, chart and table above
   * them rather than keeping a parallel copy that drifts.
   */
  apis: AnalyticsApis;

  adapt: AnalyticsAdapters;

  windowParams: Partial<Record<AnalyticsResource, WindowParamBuilder>>;

  /**
   * The values each filter may send, and how to label them.
   *
   * Two backends answer the same shapes but do not accept the same words. The
   * dashboard's `route` is a service family (`knowledge`, `integration`, …);
   * NL2SQL's is a query plan (`sql`, `cross_db`, `pandas`, `direct_answer`).
   * The screen shipped with the dashboard's list hardcoded next to the
   * dropdown, so on the NL2SQL screen every option in it was a route that
   * service has never heard of — no error, just an empty log for every choice.
   *
   * A vocabulary therefore belongs to the scope, never to the component. The
   * NL2SQL scope builds its own from the service's OpenAPI document rather than
   * restating it (see `apps/nl2sql/src/generated/analyticsContract.ts`).
   */
  filterOptions: AnalyticsFilterOptions;

  /** Page sizes and `top_n`, likewise from whatever the backend publishes. */
  limits: AnalyticsLimits;
}

const EMPTY_ADAPTERS: AnalyticsAdapters = {};
const EMPTY_WINDOW_PARAMS: Partial<
  Record<AnalyticsResource, WindowParamBuilder>
> = {};

const DEFAULT_SCOPE: AnalyticsScopeValue = {
  hiddenServices: [],
  apis: APIS,
  adapt: EMPTY_ADAPTERS,
  windowParams: EMPTY_WINDOW_PARAMS,
  filterOptions: DEFAULT_FILTER_OPTIONS,
  limits: DEFAULT_LIMITS,
};

const AnalyticsScopeContext = createContext<AnalyticsScopeValue>(DEFAULT_SCOPE);

export const useAnalyticsScope = (): AnalyticsScopeValue =>
  useContext(AnalyticsScopeContext);

export function AnalyticsScopeProvider({
  hiddenServices,
  apis,
  adapt,
  windowParams,
  filterOptions,
  limits,
  children,
}: {
  hiddenServices?: readonly string[];
  /** Partial so a product can override one route and inherit the rest. */
  apis?: Partial<AnalyticsApis>;
  adapt?: AnalyticsAdapters;
  windowParams?: Partial<Record<AnalyticsResource, WindowParamBuilder>>;
  /** Partial so a product can publish the vocabularies it knows and no more. */
  filterOptions?: Partial<AnalyticsFilterOptions>;
  limits?: Partial<AnalyticsLimits>;
  children: ReactNode;
}) {
  const value = useMemo(
    () => ({
      hiddenServices: hiddenServices ?? DEFAULT_SCOPE.hiddenServices,
      apis: apis ? { ...APIS, ...apis } : DEFAULT_SCOPE.apis,
      adapt: adapt ?? EMPTY_ADAPTERS,
      windowParams: windowParams ?? EMPTY_WINDOW_PARAMS,
      filterOptions: filterOptions
        ? { ...DEFAULT_FILTER_OPTIONS, ...filterOptions }
        : DEFAULT_FILTER_OPTIONS,
      limits: limits ? { ...DEFAULT_LIMITS, ...limits } : DEFAULT_LIMITS,
    }),
    [hiddenServices, apis, adapt, windowParams, filterOptions, limits],
  );
  return (
    <AnalyticsScopeContext.Provider value={value}>
      {children}
    </AnalyticsScopeContext.Provider>
  );
}
