"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cn } from "@/platform/utils";

interface DropdownProps {
  /** The control that opens the panel; gets the open state and a toggle. */
  trigger: (state: { open: boolean; toggle: () => void }) => ReactNode;
  /** The panel's contents; gets a closer, for menu items that act and dismiss. */
  children: (state: { close: () => void }) => ReactNode;
  /** Panels near the right edge of the toolbar hang from their right side. */
  align?: "left" | "right";
  panelClassName?: string;
  className?: string;
}

/**
 * One popover, and one way of closing it.
 *
 * The toolbar over the grid carries seven of these — columns, filters, sort,
 * type, show, export, the facet field list — and two components each running
 * their own outside-click listener is how "the button does nothing" bugs get
 * made: one listener closes the panel the other has just opened. So every panel
 * on the screen is this component, its own listener only ever closes itself,
 * and opening one closes the rest because the opening click lands outside them.
 */
export default function Dropdown({
  trigger,
  children,
  align = "left",
  panelClassName,
  className,
}: DropdownProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const close = useCallback(() => setOpen(false), []);
  const toggle = useCallback(() => setOpen((value) => !value), []);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    // Capture, so a panel inside another scroll container still closes when the
    // click is swallowed by something that stops propagation on its way up.
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={root} className={cn("relative", className)}>
      {trigger({ open, toggle })}
      {open && (
        <div
          className={cn(
            "absolute top-[calc(100%+6px)] z-40 rounded-di-lg border border-line bg-surface p-2 shadow-pop",
            align === "right" ? "right-0" : "left-0",
            panelClassName,
          )}
        >
          {children({ close })}
        </div>
      )}
    </div>
  );
}
