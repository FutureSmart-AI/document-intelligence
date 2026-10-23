import React, { memo, useEffect, useMemo, useRef } from "react";

import { X } from "lucide-react";

import Button from "./Button";

type Size = "sm" | "md" | "lg" | "xl" | "full";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: React.ReactNode;
  children: React.ReactNode;
  widthClass?: string;
  footer?: React.ReactNode;
  size?: Size;
  idPrefix?: string;
  ariaLabel?: string;
  ariaDescription?: string;
  initialFocusRef?: React.RefObject<HTMLElement>;
  closeOnEsc?: boolean;
  closeOnOverlayClick?: boolean;
  hideCloseButton?: boolean;
}

const Dialog = ({
  open,
  onClose,
  title,
  children,
  widthClass = "max-w-3xl",
  footer,
  size,
  idPrefix = "dialog",
  ariaLabel,
  ariaDescription,
  initialFocusRef,
  closeOnEsc = true,
  closeOnOverlayClick = true,
  hideCloseButton = false,
}: DialogProps) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const prevFocusedRef = useRef<HTMLElement | null>(null);

  const computedWidthClass = useMemo(() => {
    if (widthClass) return widthClass;
    switch (size) {
      case "sm":
        return "max-w-md";
      case "md":
        return "max-w-lg";
      case "lg":
        return "max-w-2xl";
      case "xl":
        return "max-w-4xl";
      case "full":
        return "max-w-[90vw]";
      default:
        return "max-w-3xl";
    }
  }, [size, widthClass]);

  const labelId = `${idPrefix}-title`;
  const descId = ariaDescription ? `${idPrefix}-description` : undefined;

  // Lock scroll while open - this must run even when open is false to restore scroll
  useEffect(() => {
    if (!open) {
      // Ensure scroll is restored when dialog is closed
      document.documentElement.style.overflow = "";
      document.body.style.overflow = "";
      return () => {
        document.documentElement.style.overflow = "";
        document.body.style.overflow = "";
      };
    }

    // Store original overflow values
    const htmlStyle = document.documentElement.style;
    const bodyStyle = document.body.style;
    const prevHtmlOverflow = htmlStyle.overflow || "";
    const prevBodyOverflow = bodyStyle.overflow || "";

    // Lock scroll on both html and body
    htmlStyle.overflow = "hidden";
    bodyStyle.overflow = "hidden";

    return () => {
      // Restore original overflow values
      htmlStyle.overflow = prevHtmlOverflow;
      bodyStyle.overflow = prevBodyOverflow;
    };
  }, [open]);

  // Manage focus on open/close
  useEffect(() => {
    if (!open)
      return () => {
        prevFocusedRef.current?.focus?.();
      };
    prevFocusedRef.current = document.activeElement as HTMLElement;
    const toFocus =
      initialFocusRef?.current ||
      panelRef.current?.querySelector<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      ) ||
      panelRef.current;
    toFocus?.focus();
    return () => {
      prevFocusedRef.current?.focus?.();
    };
  }, [initialFocusRef, open]);

  // Basic focus trap and Escape handler
  useEffect(() => {
    if (!open)
      return () => {
        prevFocusedRef.current?.focus?.();
      };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && closeOnEsc) {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const root = panelRef.current;
      if (!root) return;
      const focusables = Array.from(
        root.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter(
        (el) => !el.hasAttribute("disabled") && !el.getAttribute("aria-hidden"),
      );
      if (focusables.length === 0) {
        root.focus();
        e.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const current = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (current === first || !root.contains(current)) {
          last.focus();
          e.preventDefault();
        }
      } else {
        if (current === last) {
          first.focus();
          e.preventDefault();
        }
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [closeOnEsc, onClose, open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50" ref={containerRef}>
      <div
        className="absolute inset-0 bg-black/40"
        onClick={() => {
          if (closeOnOverlayClick) onClose();
        }}
        aria-hidden="true"
      />
      <div className="absolute inset-0 flex items-center justify-center p-4">
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={title ? labelId : undefined}
          aria-label={!title ? ariaLabel : undefined}
          aria-describedby={descId}
          tabIndex={-1}
          className={`flex max-h-[90vh] w-full ${computedWidthClass} flex-col rounded-xl border border-black/5 bg-white shadow-2xl`}
        >
          <div className="flex shrink-0 items-start justify-between border-b border-black/5 px-5 py-4">
            <div id={labelId} className="text-lg font-semibold">
              {title}
            </div>
            {!hideCloseButton && (
              <Button
                onClick={onClose}
                variant="ghost"
                size="icon"
                shape="rounded"
                className="p-2"
                startIcon={<X className="size-4" aria-hidden="true" />}
              />
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            {ariaDescription && (
              <p id={descId} className="sr-only">
                {ariaDescription}
              </p>
            )}
            {children}
          </div>
          {footer !== undefined && (
            <div className="flex shrink-0 items-center justify-end gap-3 border-t border-black/5 px-5 py-3">
              {footer}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default memo(Dialog);
