/**
 * Which of the two production hosts owns a path — decided on the client.
 *
 * `infra/cloudfront/futuresmart-agent.js` answers this for every real HTTP
 * request, but a `<Link>` or `router.push` never makes one: the SPA swaps the
 * route in place, the viewer-request function never runs, and a product path
 * reached that way stays on whichever host loaded the shell. Both hosts are
 * aliases on one distribution serving one bucket, so the wrong host renders the
 * page happily instead of failing — the split silently stops holding.
 *
 * These are the same two rules the edge function applies, so a route lands on
 * the same host whether it was typed, bookmarked, or clicked:
 *
 *  - a product path on `agent.futuresmart.ai` belongs on `app.futuresmart.ai`;
 *  - anything else on `app.futuresmart.ai` belongs on `agent.futuresmart.ai`.
 *
 * One deliberate difference from the edge: the edge sends a product path asked
 * of `agent.futuresmart.ai` to the public solution page, because a request
 * arriving there is a stale external link. A *client* navigation is not — it is
 * someone already inside the app clicking through — so it goes to the workspace
 * on `app.futuresmart.ai` rather than out to marketing.
 */

export const AGENT_HOST = "agent.futuresmart.ai";
export const APP_HOST = "app.futuresmart.ai";

export const AGENT_ORIGIN = `https://${AGENT_HOST}`;
export const APP_ORIGIN = `https://${APP_HOST}`;

/**
 * The path prefixes `app.futuresmart.ai` owns. `/nl2sql` is kept beside
 * `/database-agent` because the rename left the old path forwarding (see
 * `apps/nl2sql/src/components/shell/LegacyPathRedirect.tsx`), and that hop must
 * happen on the host that owns both.
 */
export const PRODUCT_PATH_PREFIXES = [
  "/nl2sql",
  "/database-agent",
  "/document-intelligence",
] as const;

export function isProductPath(pathname: string): boolean {
  return PRODUCT_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

/**
 * Paths that are never bounced across hosts, matching the edge function's own
 * exclusions: build assets and anything with a file extension (the product
 * pages reference unprefixed `/_next/static/…`), and the embeddable widget,
 * whose host pages name `agent.futuresmart.ai` in their CSP.
 */
export function isCrossHostExempt(pathname: string): boolean {
  return (
    pathname.startsWith("/_next/") ||
    pathname.includes(".") ||
    pathname === "/agent.js" ||
    pathname.startsWith("/agent/")
  );
}

type LocationLike = {
  hostname: string;
  pathname: string;
  search?: string;
  hash?: string;
};

/**
 * The absolute URL a location belongs at, or `null` when it is already right.
 *
 * Returns `null` for every host that is not one of the two production ones, so
 * local dev, previews and the standalone product builds are untouched.
 */
export function crossHostUrlFor(location: LocationLike): string | null {
  const { hostname, pathname } = location;
  const suffix = `${location.search ?? ""}${location.hash ?? ""}`;

  if (hostname !== AGENT_HOST && hostname !== APP_HOST) return null;
  if (isCrossHostExempt(pathname)) return null;

  const product = isProductPath(pathname);
  if (product && hostname === AGENT_HOST) {
    return `${APP_ORIGIN}${pathname}${suffix}`;
  }
  if (!product && hostname === APP_HOST) {
    return `${AGENT_ORIGIN}${pathname}${suffix}`;
  }
  return null;
}

/**
 * The href to use for a link into a product workspace.
 *
 * Absolute in a production build so the browser leaves the shell's host instead
 * of client-side navigating into a page this host does not own — an absolute
 * href makes `next/link` render a plain anchor and do a full navigation, which
 * is what puts the request in front of the edge function. Relative in
 * development, where there is only one origin.
 */
export function productUrl(pathname: string): string {
  if (process.env.NODE_ENV !== "production") return pathname;
  return `${APP_ORIGIN}${pathname}`;
}

/** The marketing site, which owns the public page for each product. */
export const SITE_ORIGIN = "https://futuresmart.ai";

/**
 * The public solution page a product path belongs to, or `null` for a path
 * that is not inside a product.
 *
 * This is the same mapping the edge function applies to a stale product URL on
 * `agent.futuresmart.ai` (`infra/cloudfront/futuresmart-agent.js`), kept here
 * for the one case the edge never sees: a logout. Signing out of a product
 * means leaving the workspace entirely, so the way out is that product's own
 * public page rather than the agent UI's home — which would only bounce the
 * signed-out viewer back to login.
 *
 * `/nl2sql` maps to the Database Agent page because it is the same product
 * under its retired prefix.
 */
export function solutionUrlForPath(pathname: string): string | null {
  if (!isProductPath(pathname)) return null;

  const slug =
    pathname === "/document-intelligence" ||
    pathname.startsWith("/document-intelligence/")
      ? "document-intelligence"
      : "database-agent";

  return `${SITE_ORIGIN}/solutions/${slug}`;
}
