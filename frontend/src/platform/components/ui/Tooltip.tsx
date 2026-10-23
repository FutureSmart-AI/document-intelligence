"use client";

import React, {
  memo,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { join } from "lodash-es";

type TooltipSide = "top" | "bottom" | "left" | "right";

export interface TooltipProps {
  children: React.ReactNode;
  content: React.ReactNode;
  side?: TooltipSide;
  delay?: number;
  disabled?: boolean;
  className?: string;
  /** Optional id for aria-describedby */
  id?: string;
}

/**
 * Generic Tooltip component.
 * Wrap any element to show a tooltip on hover/focus.
 *
 * Usage:
 * <Tooltip content="Copy ID" side="top">
 *   <button>...</button>
 * </Tooltip>
 */
export const Tooltip: React.FC<TooltipProps> = ({
  children,
  content,
  side = "top",
  delay = 120,
  disabled = false,
  className = "",
  id,
}) => {
  const [open, setOpen] = useState(false);
  const openTimerRef = useRef<number | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const triggerRef = useRef<HTMLDivElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [coords, setCoords] = useState<{ top: number; left: number }>({
    top: 0,
    left: 0,
  });

  useEffect(() => {
    return () => {
      if (openTimerRef.current) window.clearTimeout(openTimerRef.current);
      if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
    };
  }, []);

  const updatePosition = useEffectEvent(() => {
    if (!triggerRef.current || !tooltipRef.current) return;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const tipRect = tooltipRef.current.getBoundingClientRect();
    const gap = 8;
    let top = 0;
    let left = 0;

    if (side === "top") {
      top = triggerRect.top - tipRect.height - gap;
      left = triggerRect.left + triggerRect.width / 2 - tipRect.width / 2;
    } else if (side === "bottom") {
      top = triggerRect.bottom + gap;
      left = triggerRect.left + triggerRect.width / 2 - tipRect.width / 2;
    } else if (side === "left") {
      top = triggerRect.top + triggerRect.height / 2 - tipRect.height / 2;
      left = triggerRect.left - tipRect.width - gap;
    } else {
      // right
      top = triggerRect.top + triggerRect.height / 2 - tipRect.height / 2;
      left = triggerRect.right + gap;
    }

    // Clamp within viewport with 8px padding
    const padding = 8;
    const maxLeft = window.innerWidth - tipRect.width - padding;
    const maxTop = window.innerHeight - tipRect.height - padding;
    top = Math.max(padding, Math.min(top, maxTop));
    left = Math.max(padding, Math.min(left, maxLeft));

    setCoords({ top, left });
  });

  useLayoutEffect(() => {
    if (!open) return () => {};
    // wait for tooltip to mount then measure
    const r = requestAnimationFrame(updatePosition);
    const onScroll = () => updatePosition();
    const onResize = () => updatePosition();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(r);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open, side, content]);

  const show = () => {
    if (disabled) return;
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
    openTimerRef.current = window.setTimeout(() => setOpen(true), delay);
  };
  const hide = () => {
    if (openTimerRef.current) window.clearTimeout(openTimerRef.current);
    closeTimerRef.current = window.setTimeout(() => setOpen(false), 60);
  };

  if (!content) {
    return children;
  }

  return (
    <div
      ref={triggerRef}
      className={`relative inline-flex w-[-webkit-fill-available] ${className}`}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      aria-describedby={id}
    >
      {children}
      {!disabled && open
        ? createPortal(
            <div
              ref={tooltipRef}
              id={id}
              role="tooltip"
              style={{
                top: coords.top,
                left: coords.left,
                zIndex: 99999,
                maxWidth: "300px",
              }}
              className={join(
                [
                  "pointer-events-none fixed whitespace-normal wrap-break-word rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 shadow-lg ring-1 ring-black/5",
                  "opacity-100 transition-opacity duration-150",
                  className,
                ],
                " ",
              )}
            >
              <div className="relative">{content}</div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
};

export default memo(Tooltip);
