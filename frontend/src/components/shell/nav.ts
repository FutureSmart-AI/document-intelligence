import type { IconName } from "@/components/Icon";
import { APP, collectionRoute, ROUTES, type Route } from "@/lib/routes";

export interface NavItem {
  href: Route;
  label: string;
  icon: IconName;
  /** Title shown in the topbar when this item is current. */
  title: string;
  /**
   * Home is the collection list and deliberately drops the collection — leaving
   * the collection is what it is for. Every other item is scoped to one
   * collection and carries its `?collection=`, so the rail cannot silently
   * switch which documents you are looking at.
   */
  scope?: "workspace";
  /**
   * Kept as a destination but taken out of the rail: the screen still answers on
   * its own route, so a saved link keeps working and the topbar still knows what
   * to call it, but nothing points at it from the navigation.
   */
  hidden?: boolean;
}

/**
 * Every destination the shell knows, in the artifact's order. Data, Explore,
 * Batch reports and Analytics are `hidden`: the screens still answer on their
 * own routes and the topbar still titles them, but the rail no longer offers
 * them.
 */
export const NAV_ITEMS: NavItem[] = [
  {
    href: ROUTES.home,
    label: "Home",
    icon: "home",
    title: "Document Intelligence",
    scope: "workspace",
  },
  {
    href: ROUTES.knowledge,
    label: "Knowledge",
    icon: "book",
    title: "Documents",
  },
  {
    href: ROUTES.data,
    label: "Data",
    icon: "tbl",
    title: "Structured data",
    hidden: true,
  },
  {
    href: ROUTES.explore,
    label: "Explore",
    icon: "compass",
    title: "Explore",
    hidden: true,
  },
  {
    href: ROUTES.conversations,
    label: "Conversations",
    icon: "chat",
    title: "Conversations",
  },
  {
    href: ROUTES.reports,
    label: "Batch reports",
    icon: "clipboard",
    title: "Batch reports",
    hidden: true,
  },
  {
    href: ROUTES.analytics,
    label: "Analytics",
    icon: "chart",
    title: "Analytics",
    hidden: true,
  },
  { href: ROUTES.share, label: "Share", icon: "share", title: "Share" },
  {
    href: ROUTES.settings,
    label: "Settings",
    icon: "cog",
    title: "Settings",
  },
];

/** The items the rail actually draws — every destination that is not hidden. */
export const RAIL_ITEMS: NavItem[] = NAV_ITEMS.filter((item) => !item.hidden);

export function navHref(item: NavItem, collectionId?: string): string {
  return item.scope === "workspace" || !collectionId
    ? item.href
    : collectionRoute(item.href, collectionId);
}

/** Longest-prefix match, so a nested route still highlights its rail entry. */
export function currentNavItem(pathname: string): NavItem | null {
  const normalized = pathname.replace(/\/+$/, "") || APP;
  return (
    NAV_ITEMS.filter(
      (item) =>
        normalized === item.href || normalized.startsWith(`${item.href}/`),
    ).sort((a, b) => b.href.length - a.href.length)[0] ?? null
  );
}
