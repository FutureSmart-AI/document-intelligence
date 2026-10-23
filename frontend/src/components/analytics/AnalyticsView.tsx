"use client";

import { useMemo, type ComponentType } from "react";
import dynamic from "next/dynamic";

import type { UseQueryResult } from "@tanstack/react-query";

import {
  KNOWLEDGE_ANALYTICS_ADAPTERS,
  KNOWLEDGE_ANALYTICS_APIS,
  KNOWLEDGE_ANALYTICS_LIMITS,
  KNOWLEDGE_FILTER_OPTIONS,
  KNOWLEDGE_WINDOW_PARAMS,
} from "@/data/analytics";
import { AnalyticsScopeProvider } from "@/platform/analytics/AnalyticsScope";
import AnalyticsToolbar from "@/platform/analytics/components/AnalyticsToolbar";
import NoAnalyticsYetBanner from "@/platform/analytics/components/NoAnalyticsYetBanner";
import TabNav from "@/platform/analytics/components/TabNav";
import { useAnalyticsWindow } from "@/platform/analytics/hooks/useAnalyticsWindow";
import { useScorecard } from "@/platform/analytics/hooks/useScorecard";
import {
  computeScorecardDeltas,
  type ScorecardDeltas,
} from "@/platform/analytics/lib/scorecardDeltas";
import type { AnalyticsWindow, Scorecard } from "@/platform/analytics/types";
import {
  buildPreviousWindow,
  emptyScorecard,
  getHttpStatus,
} from "@/platform/analytics/utils";
import { useAuth } from "@/platform/hooks/useAuth";

import {
  DI_ANALYTICS_TAB_KEYS,
  DI_ANALYTICS_TABS,
  type DiTabKey,
} from "./constants";

/**
 * One collection's analytics — the agent dashboard's analytics page, on this
 * service's numbers.
 *
 * Not a copy of that page: the toolbar, tab bar, KPI cards, charts and tables
 * are the dashboard's own components. Only the endpoints differ, and they are
 * injected through `AnalyticsScopeProvider`: the dashboard reads its own
 * dashboard/v1/agents/{id}/… routes, which are keyed by chat-registry ids and
 * know nothing about a knowledge base, while this service answers the same
 * information under `…/{knowledge_id}/health/…`. Because the components are
 * shared rather than duplicated, the two screens cannot drift apart.
 *
 * The `.di-analytics` wrapper is what makes them look like this product: those
 * components are painted in literal slate/indigo utilities, and that class
 * repaints exactly those palettes from this app's tokens inside its own subtree
 * (see `styles/theme.css`), light and dark alike.
 *
 * The one addition is Model performance, which has no dashboard equivalent: it
 * reads the scorecard's per-(step, model) aggregates, which that page only ever
 * shows as a single per-node breakdown.
 */

const OverviewTab = dynamic(
  () => import("@/platform/analytics/components/tabs/OverviewTab"),
  { ssr: false },
);
const QualityTab = dynamic(
  () => import("@/platform/analytics/components/tabs/QualityTab"),
  { ssr: false },
);
const PerformanceTab = dynamic(
  () => import("@/platform/analytics/components/tabs/PerformanceTab"),
  { ssr: false },
);
const CostTab = dynamic(
  () => import("@/platform/analytics/components/tabs/CostTab"),
  { ssr: false },
);
const QueryLogsTab = dynamic(
  () => import("@/platform/analytics/components/tabs/QueryLogsTab"),
  { ssr: false },
);
const ModelPerformanceTab = dynamic(
  () => import("./tabs/ModelPerformanceTab"),
  { ssr: false },
);

type TabProps = {
  scorecard: UseQueryResult<Scorecard>;
  window: AnalyticsWindow;
  agentId?: string | null;
  onTabChange?: (tab: DiTabKey) => void;
  deltas?: ScorecardDeltas;
};

const tabComponents: Record<DiTabKey, ComponentType<TabProps>> = {
  overview: OverviewTab as ComponentType<TabProps>,
  quality: QualityTab as ComponentType<TabProps>,
  performance: PerformanceTab as ComponentType<TabProps>,
  cost: CostTab as ComponentType<TabProps>,
  models: ModelPerformanceTab as ComponentType<TabProps>,
  querylogs: QueryLogsTab as ComponentType<TabProps>,
};

function AnalyticsScreen({ collectionId }: { collectionId: string }) {
  const { window, setWindow, activeTab, setTab } = useAnalyticsWindow<DiTabKey>(
    DI_ANALYTICS_TAB_KEYS,
  );
  // No `bucket`: `/health/scorecard` publishes no granularity parameter, and
  // `usage.per_day` is daily. Sending one would be sending a parameter the
  // route does not have.
  const scorecard = useScorecard(collectionId, window);

  // A second scorecard for the preceding window, so every KPI card can show a
  // "vs previous period" pill. Until the current one resolves the previous
  // window is unknown, so this falls back to `window` — same query key, no
  // extra request.
  const prevWindow = useMemo(
    () => buildPreviousWindow(scorecard.data),
    [scorecard.data],
  );
  const previousScorecard = useScorecard(collectionId, prevWindow ?? window);
  const deltas = useMemo(
    () =>
      prevWindow
        ? computeScorecardDeltas(scorecard.data, previousScorecard.data)
        : undefined,
    [prevWindow, scorecard.data, previousScorecard.data],
  );

  // The "vs …" label comes from the window the deltas were actually computed
  // against — labelling one span while the pills measure another is how a
  // reader gets a wrong answer out of correct numbers.
  const previousSince =
    prevWindow?.mode === "custom" ? prevWindow.start : undefined;

  // A collection nobody has asked anything yet has no analytics: the routes
  // answer 404, which is the documented first-run state rather than an error.
  // Render the whole page with every metric at zero so the layout on day one is
  // the layout on day thirty.
  const isNoData = scorecard.isError && getHttpStatus(scorecard.error) === 404;
  const scorecardForTabs = (
    isNoData
      ? {
          ...scorecard,
          data: emptyScorecard(collectionId, window),
          isError: false,
          isLoading: false,
          isSuccess: true,
          status: "success",
        }
      : scorecard
  ) as UseQueryResult<Scorecard>;

  const ActiveTab = tabComponents[activeTab];

  return (
    <div className="di-analytics">
      {/*
       * No page heading, deliberately: `AppShell` already titles this screen in
       * the topbar, and the dashboard's analytics page opens straight onto its
       * window bar. A second heading would push the tabs out of parity and give
       * the page two titles for one thing.
       */}
      <AnalyticsToolbar
        window={window}
        onWindowChange={setWindow}
        since={scorecardForTabs.data?.since}
        until={scorecardForTabs.data?.until}
        previousSince={previousSince}
      />

      <div className="pt-4">
        <TabNav<DiTabKey>
          tabs={DI_ANALYTICS_TABS}
          activeTab={activeTab}
          onChange={setTab}
        />
      </div>

      {isNoData && (
        <div className="pt-4">
          <NoAnalyticsYetBanner />
        </div>
      )}

      <div className="pt-4">
        <ActiveTab
          scorecard={scorecardForTabs}
          window={window}
          agentId={collectionId}
          onTabChange={setTab}
          deltas={deltas}
        />
      </div>
    </div>
  );
}

export default function AnalyticsView() {
  // A collection *is* the knowledge base, so the auth bridge's `agentId` is the
  // id these routes are keyed by — the same seam every other view here reads.
  const { agentId } = useAuth();

  if (!agentId) return null;

  return (
    <AnalyticsScopeProvider
      apis={KNOWLEDGE_ANALYTICS_APIS}
      adapt={KNOWLEDGE_ANALYTICS_ADAPTERS}
      windowParams={KNOWLEDGE_WINDOW_PARAMS}
      filterOptions={KNOWLEDGE_FILTER_OPTIONS}
      limits={KNOWLEDGE_ANALYTICS_LIMITS}
    >
      <AnalyticsScreen collectionId={agentId} />
    </AnalyticsScopeProvider>
  );
}
