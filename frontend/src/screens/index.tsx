"use client";

import { Suspense, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";

import AnalyticsView from "@/components/analytics/AnalyticsView";
import ConversationsView from "@/components/conversations/ConversationsView";
import CreateStepper from "@/components/create/CreateStepper";
import CredentialsView from "@/components/credentials/CredentialsView";
import DataView from "@/components/data/DataView";
import ExploreScreenBody from "@/components/explore/ExploreScreenBody";
import HomeView from "@/components/home/HomeView";
import KnowledgeView from "@/components/knowledge/KnowledgeView";
import ReportsView from "@/components/reports/ReportsView";
import SettingsView from "@/components/settings/SettingsView";
import ShareView from "@/components/share/ShareView";
import AppShell from "@/components/shell/AppShell";
import CollectionScope from "@/components/shell/CollectionScope";
import UsageView from "@/components/usage/UsageView";
import { COLLECTION_PARAM, ROUTES, type Route } from "@/lib/routes";

/**
 * One component per screen, chrome included.
 *
 * Each route under `src/app/document-intelligence/` is a three-line file over
 * one of these, so the shell, the collection guard and the view for a screen
 * live in one place.
 */

/** Reads `?collection=` and hands it to the shell and the guard. */
function CollectionScreen({
  route,
  purpose,
  flush,
  children,
}: {
  route: Route;
  purpose: string;
  /** Views that own their own scrolling (Conversations) drop the view padding. */
  flush?: boolean;
  children: ReactNode;
}) {
  const collectionId = useSearchParams().get(COLLECTION_PARAM) ?? undefined;
  return (
    <AppShell
      collectionId={collectionId}
      flush={flush && Boolean(collectionId)}
    >
      <CollectionScope
        collectionId={collectionId}
        route={route}
        purpose={purpose}
      >
        {children}
      </CollectionScope>
    </AppShell>
  );
}

export function HomeScreen() {
  return (
    <AppShell>
      <HomeView />
    </AppShell>
  );
}

/**
 * Creating a collection is workspace scope: there is no collection to scope it
 * to until it exists, so the shell draws no rail and no breadcrumb — the same
 * treatment Home and Usage get.
 */
export function CreateScreen() {
  return (
    <AppShell>
      <CreateStepper />
    </AppShell>
  );
}

export function KnowledgeScreen() {
  return (
    <Suspense>
      <CollectionScreen
        route={ROUTES.knowledge}
        purpose="Documents belong to one collection. Pick the one you want to add to or read."
      >
        <KnowledgeView />
      </CollectionScreen>
    </Suspense>
  );
}

export function DataScreen() {
  return (
    <Suspense>
      <CollectionScreen
        route={ROUTES.data}
        purpose="The table is one collection's documents in its own schema. Pick the collection you want to sort."
      >
        <DataView />
      </CollectionScreen>
    </Suspense>
  );
}

export function ExploreScreen() {
  return (
    <Suspense>
      <CollectionScreen
        route={ROUTES.explore}
        purpose="Values are counted within one collection. Pick the one you want to look through."
      >
        <ExploreScreenBody />
      </CollectionScreen>
    </Suspense>
  );
}

export function ConversationsScreen() {
  return (
    <Suspense>
      <CollectionScreen
        route={ROUTES.conversations}
        purpose="A conversation is with one collection's documents. Pick the collection you want to ask about."
        flush
      >
        <ConversationsView />
      </CollectionScreen>
    </Suspense>
  );
}

export function ReportsScreen() {
  return (
    <Suspense>
      <CollectionScreen
        route={ROUTES.reports}
        purpose="A report asks its questions of one collection. Pick the one you want answers from."
      >
        <ReportsView />
      </CollectionScreen>
    </Suspense>
  );
}

export function AnalyticsScreen() {
  return (
    <Suspense>
      <CollectionScreen
        route={ROUTES.analytics}
        purpose="These numbers are one collection's. Pick the one you want to measure."
      >
        <AnalyticsView />
      </CollectionScreen>
    </Suspense>
  );
}

export function ShareScreen() {
  return (
    <Suspense>
      <CollectionScreen
        route={ROUTES.share}
        purpose="Sharing hands out one collection. Pick the one you want people to reach."
      >
        <ShareView />
      </CollectionScreen>
    </Suspense>
  );
}

export function SettingsScreen() {
  return (
    <Suspense>
      <CollectionScreen
        route={ROUTES.settings}
        purpose="Settings apply to one collection. Pick the one you want to change."
      >
        <SettingsView />
      </CollectionScreen>
    </Suspense>
  );
}

/**
 * Usage and Credentials are account-level, so they pass no collection: the
 * shell shows the board's chrome rather than a rail scoped to a collection
 * neither screen is about. Usage carries its own rail for its three sections.
 */
export function UsageScreen() {
  return (
    // Suspense because the view opens on the tab `?tab=` names — a static
    // export has to bail out to the client to read a search param.
    <Suspense>
      <AppShell>
        <UsageView />
      </AppShell>
    </Suspense>
  );
}

export function CredentialsScreen() {
  return (
    <AppShell>
      <CredentialsView />
    </AppShell>
  );
}
