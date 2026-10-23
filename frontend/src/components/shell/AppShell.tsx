"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { BrandMark, BrandWord } from "@/components/Brand";
import CollapseHandle from "@/components/CollapseHandle";
import Icon from "@/components/Icon";
import ThemeToggle from "@/components/ThemeToggle";
import { IconBtn } from "@/components/ui";
import AuthBridge from "@/data/AuthBridge";
import { useCollection, useCollections } from "@/data/collections";
import {
  RAIL_COLLAPSED_WIDTH,
  RAIL_MAX_WIDTH,
  RAIL_MIN_WIDTH,
  RAIL_WIDTH,
  TOPBAR_HEIGHT,
} from "@/lib/constants";
import { DOC_FONT_CLASS } from "@/lib/fonts";
import { collectionRoute, ROUTES } from "@/lib/routes";
import ProfileDropdown from "@/platform/components/rag/dashboard/profile-modal";
import Tooltip from "@/platform/components/ui/Tooltip";
import { useResizablePanel } from "@/platform/hooks/useResizablePanel";
import { useSidebarCollapsed } from "@/platform/hooks/useSidebarCollapsed";
import { cn } from "@/platform/utils";

import { currentNavItem, navHref, RAIL_ITEMS } from "./nav";

interface AppShellProps {
  children: ReactNode;
  /**
   * The collection this view is scoped to, from `?collection=`. Home passes
   * nothing: without a collection the shell shows neither the breadcrumb nor the
   * rail, since the rail navigates *within* a collection and there is none.
   */
  collectionId?: string;
  /** Views that own their own scrolling (Conversations) drop the view padding. */
  flush?: boolean;
}

/** Jump straight to a collection by name, from anywhere in the product. */
function CollectionSearch({ collectionId }: { collectionId?: string }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const { collections } = useCollections();

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    return collections
      .filter((collection) => collection.name.toLowerCase().includes(needle))
      .slice(0, 6);
  }, [collections, query]);

  return (
    <div className="relative hidden md:block">
      <div className="flex w-57.5 items-center gap-1.5 rounded-di-md border border-line bg-sunken px-2.5 py-1.5 text-ink-3">
        <Icon name="search" className="size-3.5" />
        <input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          placeholder="Search your collections…"
          aria-label="Search your collections"
          className="w-full border-0 bg-transparent text-[13px] text-ink outline-none"
        />
      </div>
      {open && matches.length > 0 && (
        <ul className="absolute top-[calc(100%+6px)] left-0 z-30 w-70 list-none rounded-di-lg border border-line bg-surface p-1.5 shadow-pop">
          {matches.map((collection) => (
            <li key={collection.id}>
              <button
                type="button"
                onMouseDown={() => {
                  setQuery("");
                  router.push(collectionRoute(ROUTES.knowledge, collection.id));
                }}
                className={cn(
                  "w-full cursor-pointer rounded-di-md px-2.5 py-2 text-left text-[13px] hover:bg-sunken",
                  collection.id === collectionId
                    ? "font-semibold text-accent"
                    : "text-ink",
                )}
              >
                {collection.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Shell({ children, collectionId, flush }: AppShellProps) {
  const pathname = usePathname() ?? ROUTES.home;
  const current = currentNavItem(pathname);
  const { collection } = useCollection(collectionId);
  // The agent UI's own rail behaviour, from its own hooks: one stored collapsed
  // state and one stored width shared by every product, and the same
  // collapse-below-1024px rule. A rail that collapses here and not there is the
  // kind of difference that reads as a different application.
  const { sidebarCollapsed, toggleSidebarCollapsed } = useSidebarCollapsed();
  const {
    width: railDragWidth,
    resetWidth: resetRailWidth,
    isResizing: isRailResizing,
    startResize: startRailResize,
    setWidth: setRailWidth,
  } = useResizablePanel({
    storageKey: "sidebar-width",
    defaultWidth: RAIL_WIDTH,
    minWidth: RAIL_MIN_WIDTH,
    maxWidth: RAIL_MAX_WIDTH,
    edge: "right",
  });
  // Both hooks seed themselves from `localStorage` and the window width,
  // neither of which exists when these pages are prerendered — so the rail
  // renders expanded until mount and the two markups agree.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const collapsed = mounted && sidebarCollapsed;
  const railWidth = collapsed ? RAIL_COLLAPSED_WIDTH : railDragWidth;

  // The rail is the "you are inside a collection" signal, so Home has none at
  // all — exactly as in the artifact.
  const showRail = Boolean(collectionId) && current?.scope !== "workspace";

  return (
    // `di-root` scopes this product's base rules — type, form colours,
    // selection, focus ring — to its own subtree, so the shared views under
    // `src/platform/` keep their own palette. The font class rides here too, so
    // the product's faces are in scope for every screen.
    <div
      className={cn(
        DOC_FONT_CLASS,
        "di-root flex h-screen min-h-[640px] flex-col bg-bg",
      )}
    >
      <header className="flex h-14 flex-none items-center gap-4 border-b border-line bg-surface px-4">
        <Link
          href={ROUTES.home}
          title="Back to all collections"
          className="flex flex-none items-center gap-2"
        >
          <BrandMark />
          <BrandWord className="hidden sm:inline" />
        </Link>

        {collectionId && (
          <>
            <span className="text-ink-3">›</span>
            <span className="flex min-w-0 items-center gap-1.5 text-[13.5px] text-ink-2">
              <span
                className={cn(
                  "size-1.5 shrink-0 rounded-full",
                  collection?.knowledgeEnabled
                    ? "bg-success-dot"
                    : "bg-warn-dot",
                )}
              />
              <span className="truncate">
                {collection?.name ?? "Collection"}
              </span>
            </span>
          </>
        )}

        <div className="flex-1" />

        <CollectionSearch collectionId={collectionId} />
        <Link
          href="https://futuresmart.ai/solutions/document-intelligence"
          className="flex-none"
        >
          <IconBtn label="Back to the site" tabIndex={-1}>
            <Icon name="globe" className="size-3.75" />
          </IconBtn>
        </Link>
        <ThemeToggle />
        <ProfileDropdown collapsed={false} inHeader />
      </header>

      <div className="flex min-h-0 flex-1">
        {showRail && (
          <nav
            aria-label="Collection"
            data-demo="rail"
            className={cn(
              "relative hidden flex-none flex-col gap-0.5 overflow-visible border-r border-line bg-surface px-2.5 py-3.5 md:flex",
              // While the edge is being dragged the width must track the
              // pointer exactly, so the transition is only for expand/collapse.
              !isRailResizing && "transition-[width] duration-200 ease-in-out",
            )}
            style={{ width: railWidth }}
          >
            {RAIL_ITEMS.map((item) => {
              const isActive = item.href === current?.href;
              return (
                <Tooltip
                  key={item.href}
                  content={item.label}
                  side="right"
                  // No `className` here: `Tooltip` puts it on the bubble as
                  // well as on the wrapper, and a full-width bubble is not a
                  // tooltip. The wrapper stretches on its own as a column flex
                  // child.
                  disabled={!collapsed}
                >
                  <Link
                    href={navHref(item, collectionId)}
                    aria-current={isActive ? "page" : undefined}
                    title={collapsed ? item.label : undefined}
                    className={cn(
                      "relative flex w-full items-center gap-2.5 rounded-di-md px-2.5 py-2 text-[13.5px] font-medium whitespace-nowrap",
                      collapsed && "justify-center",
                      isActive
                        ? "bg-accent-soft font-semibold text-accent-strong"
                        : "text-ink-2 hover:bg-sunken hover:text-ink",
                    )}
                  >
                    {isActive && (
                      <span className="absolute top-2 bottom-2 -left-2.5 w-0.75 rounded-r-[3px] bg-accent" />
                    )}
                    <Icon
                      name={item.icon}
                      className="size-4.25 shrink-0"
                      strokeWidth={1.7}
                    />
                    {!collapsed && item.label}
                  </Link>
                </Tooltip>
              );
            })}

            {!collapsed && (
              <div className="mt-auto border-t border-line-soft p-2.5 text-[11.5px] text-ink-3">
                {collection?.name}
                <br />
                Same APIs · same backend
              </div>
            )}

            {/* The agent UI's floating handle, in this product's colours: the
                one control that both collapses the rail and brings it back, so
                it has to sit on the edge rather than inside a rail that may be
                72px wide. */}
            <CollapseHandle
              collapsed={collapsed}
              onToggle={toggleSidebarCollapsed}
              label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            />

            {!collapsed && (
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize sidebar"
                tabIndex={0}
                onPointerDown={startRailResize}
                onDoubleClick={resetRailWidth}
                onKeyDown={(event) => {
                  if (event.key === "ArrowLeft") {
                    setRailWidth(railDragWidth - 24);
                  }
                  if (event.key === "ArrowRight") {
                    setRailWidth(railDragWidth + 24);
                  }
                }}
                title="Drag to resize — double-click to reset"
                className={cn(
                  "absolute top-0 right-0 z-10 h-full w-1.5 translate-x-1/2 cursor-col-resize",
                  "after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-transparent",
                  "hover:after:bg-accent",
                  isRailResizing && "after:bg-accent",
                )}
              />
            )}
          </nav>
        )}

        {/* No signed-out branch here: `RequireSession` in the route layout owns
            that, in both mounts. Two components deciding whether you are signed
            in is how one of them ends up showing an empty board to someone whose
            session has simply not resolved yet. */}
        <main className="relative min-w-0 flex-1 overflow-auto">
          <div className={flush ? "h-full" : "px-6 py-6"}>{children}</div>
        </main>
      </div>
    </div>
  );
}

/**
 * The product chrome: topbar, collection rail, and the agent UI's auth context.
 *
 * Every view renders inside that context, so the shared components this product
 * mounts — the profile menu, the documents list, the whole chat — find the
 * knowledge base and viewer they expect without each page wiring it up.
 */
export default function AppShell(props: AppShellProps) {
  return (
    <AuthBridge collectionId={props.collectionId}>
      <Shell {...props} />
    </AuthBridge>
  );
}

export { TOPBAR_HEIGHT };
