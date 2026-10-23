"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useIsMobile } from "@/platform/hooks/useIsMobile";
import { useResizablePanel } from "@/platform/hooks/useResizablePanel";
import { cn } from "@/platform/utils";

import { FOCUSABLE_SELECTOR } from "./constants";

/** Width the list beside the docked panel is never allowed to drop below. */
const MIN_LIST_WIDTH = 360;

type SidePreviewProps = {
  open: boolean;
  onClose: () => void;
  /** Full-screen mode — covers the viewport at every breakpoint. */
  expanded?: boolean;
  /** Called by Escape while expanded, so it collapses before it closes. */
  onCollapse?: () => void;
  children?: React.ReactNode;
  /** Docked width defaults; the user's dragged width is persisted. */
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  storageKey?: string;
  /** Accessible name for the panel in both docked and modal presentations. */
  label?: string;
};

/**
 * Right-side preview panel.
 *
 * Docked (desktop, collapsed) it is a plain inline flex child: no full-viewport
 * wrapper, so the list next to it shrinks, stays scrollable and stays
 * clickable — users switch documents and re-filter without closing the preview.
 * Mobile (<md) and the expanded full-screen view are true modals: backdropped,
 * focus-trapped and viewport-covering.
 *
 * The panel keeps the *same* place in the tree in every presentation and only
 * switches its positioning classes, so expanding/collapsing (or crossing the md
 * breakpoint) never remounts `children` and never drops their scroll position or
 * in-flight state. Nothing on these pages establishes a containing block for
 * fixed positioning, so `fixed inset-0` still covers the viewport from here.
 */
export function SidePreview({
  open,
  onClose,
  expanded = false,
  onCollapse,
  children,
  defaultWidth = 460,
  minWidth = 340,
  maxWidth = 900,
  storageKey = "document-preview-width",
  label = "Document preview",
}: SidePreviewProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const isMobile = useIsMobile();
  const isModal = isMobile || expanded;

  // The list column must keep a usable width, so the drag stops short of the
  // viewport edge. Clamping here (rather than with a `max-w-*` class) keeps the
  // panel tracking the pointer all the way to its real limit instead of
  // appearing to stick.
  const [viewportWidth, setViewportWidth] = useState(0);
  useEffect(() => {
    const measure = () => setViewportWidth(window.innerWidth);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const effectiveMaxWidth = viewportWidth
    ? Math.max(minWidth, Math.min(maxWidth, viewportWidth - MIN_LIST_WIDTH))
    : maxWidth;

  const { width, setWidth, resetWidth, isResizing, startResize } =
    useResizablePanel({
      storageKey,
      defaultWidth,
      minWidth,
      maxWidth: effectiveMaxWidth,
      edge: "left",
    });

  const dockedWidth = Math.min(width, effectiveMaxWidth);

  // Only the modal presentations own focus. Docked, the page stays interactive,
  // so pulling focus off the row the user just activated would be a regression.
  useEffect(() => {
    if (!open || !isModal) return undefined;
    const timer = setTimeout(() => panelRef.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, [open, isModal]);

  // Expanded collapses back to docked before it closes, so Escape unwinds the
  // presentation one step at a time.
  const closeOrCollapse = useCallback(() => {
    if (expanded && onCollapse) {
      onCollapse();
      return;
    }
    onClose();
  }, [expanded, onCollapse, onClose]);

  // Modal: Escape anywhere dismisses, because focus is inside the modal anyway
  // and this keeps working before the user has interacted. Docked, Escape is
  // handled on the panel instead (see `handlePanelKeyDown`) so pressing it in
  // the search box or an unrelated control doesn't close the preview.
  useEffect(() => {
    if (!open || !isModal) return undefined;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeOrCollapse();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, isModal, closeOrCollapse]);

  const handlePanelKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      // Docked, Escape only counts when focus is inside the panel; the document
      // listener above owns the modal case.
      if (!isModal && e.key === "Escape") {
        closeOrCollapse();
        return;
      }

      // Trapping Tab is only correct for the modal presentations. Docked, the
      // toolbar and table beside the panel must remain keyboard-reachable.
      if (!isModal || e.key !== "Tab" || !panelRef.current) return;

      const focusable =
        panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    },
    [isModal, closeOrCollapse],
  );

  if (!open) return null;

  return (
    <>
      {isModal && (
        <div
          data-testid="side-preview-backdrop"
          className="fixed inset-0 z-9998 bg-black/40 backdrop-blur-[2px] animate-in fade-in duration-200"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <div
        ref={panelRef}
        tabIndex={-1}
        onKeyDown={handlePanelKeyDown}
        role={isModal ? "dialog" : "region"}
        aria-modal={isModal ? "true" : undefined}
        aria-label={label}
        style={isModal ? undefined : { width: dockedWidth }}
        className={cn(
          "flex flex-col bg-white outline-none",
          isModal
            ? cn(
                "fixed inset-0 z-9999 h-full w-full",
                expanded
                  ? "animate-in fade-in duration-200"
                  : "animate-in slide-in-from-right duration-200",
              )
            : cn(
                "relative h-full shrink-0 border-l border-slate-200",
                "shadow-[-25px_0_50px_-12px_rgba(0,0,0,0.08)]",
                "animate-in slide-in-from-right duration-200",
                isResizing && "select-none",
              ),
        )}
      >
        {!isModal && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize preview"
            tabIndex={0}
            onPointerDown={startResize}
            onDoubleClick={resetWidth}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft") setWidth(dockedWidth + 24);
              if (e.key === "ArrowRight") setWidth(dockedWidth - 24);
            }}
            title="Drag to resize — double-click to reset"
            className={cn(
              "absolute left-0 top-0 z-10 h-full w-1.5 -translate-x-1/2 cursor-col-resize",
              "after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-transparent",
              "hover:after:bg-indigo-400",
              isResizing && "after:bg-indigo-500",
            )}
          />
        )}
        {children}
      </div>
    </>
  );
}
