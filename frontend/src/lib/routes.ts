/**
 * Where this app lives on `app.futuresmart.ai`.
 *
 * The whole product sits under `/document-intelligence` inside the main
 * application — the collection board *is* the entry point, so there is no
 * marketing page above it and no `/dashboard` segment below it. Every link goes
 * through these constants, so the product stays one mounted microfrontend
 * rather than a separately hosted application.
 *
 * Deliberately *not* under `/dashboard/…`, which is the agent UI's own route
 * group: everything there renders inside that app's sidebar, topbar and
 * `AuthProvider`, so mounting this product's shell in it would nest one chrome
 * inside another. `apps/nl2sql` sits at `/nl2sql` for the same reason.
 *
 * The build is a static export, so there are no dynamic route segments: a
 * collection is carried in `?collection=<knowledge_id>` instead.
 */

/**
 * The host this product is served from, used by the snippets people copy off
 * the Share screen.
 *
 * Deliberately not the API base: in dev that points at the local proxy, and a
 * snippet carrying `localhost:4000` is useless to whoever pastes it. With no
 * override configured it is wherever the page is actually served from, so a
 * staging build cannot hand out production URLs.
 */
export function publicHost(): string {
  const configured = process.env.NEXT_PUBLIC_PUBLIC_HOST;
  if (configured) return configured.replace(/\/+$/, "");
  return typeof window === "undefined" ? "" : window.location.origin;
}

/** The product entry point — the list of collections. */
export const APP = "/document-intelligence";

export const ROUTES = {
  home: APP,
  /**
   * Making a collection is a screen, not a dialog: all of its steps are on
   * screen from the first one, so what is coming is visible before you commit
   * to it. Workspace scope — you are not inside a collection until it exists.
   */
  create: `${APP}/create`,
  knowledge: `${APP}/knowledge`,
  data: `${APP}/data`,
  explore: `${APP}/explore`,
  conversations: `${APP}/conversations`,
  /**
   * A set of questions run together against one collection, and the runs of it.
   * Named for what it produces rather than for the machinery, the way the
   * NL2SQL product names the same screen.
   */
  reports: `${APP}/batch-query-reports`,
  /** Ways out of one collection: the bubble, the full-page link, the API. */
  share: `${APP}/share`,
  /** Quality, speed, cost and every question one collection has been asked. */
  analytics: `${APP}/analytics`,
  settings: `${APP}/settings`,
  /**
   * The one account-level screen, reached from the board rather than the rail:
   * the rail navigates within one collection, and usage is not about one.
   * (`apps/nl2sql` puts its Usage in the same place, for the same reason.)
   *
   * Credentials sits beside it for the same reason: keys are stored per
   * account, not per collection, so the screen never asks you to pick one.
   */
  usage: `${APP}/usage`,
  credentials: `${APP}/credentials`,
} as const;

export type Route = (typeof ROUTES)[keyof typeof ROUTES];

/** The Usage screen's tab, when a link needs to open one that is not the first. */
export const TAB_PARAM = "tab";

/**
 * Usage, opened on Limits.
 *
 * Where a quota refusal sends you: the allowance that refused, its ceiling and
 * when it resets are all on that tab, and landing on Overview instead would
 * make the reader hunt for the thing the toast just told them about.
 */
export const LIMITS_TAB = `${ROUTES.usage}?${TAB_PARAM}=limits`;

/** The query parameter every collection-scoped route carries. */
export const COLLECTION_PARAM = "collection";

/** A product route scoped to one collection. */
export function collectionRoute(route: Route, collectionId: string): string {
  return `${route}?${COLLECTION_PARAM}=${encodeURIComponent(collectionId)}`;
}
