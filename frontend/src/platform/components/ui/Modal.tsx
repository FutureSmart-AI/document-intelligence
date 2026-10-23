import React, {
  useCallback,
  useEffect,
  useEffectEvent,
  useId,
  useRef,
} from "react";
import { createPortal } from "react-dom";

import { Loader2, X } from "lucide-react";

import { cn } from "@/platform/utils";

import { FOCUSABLE_SELECTOR, MAX_WIDTH_CLASSES } from "./constants";

type ModalProps = {
  open: boolean;
  title?: React.ReactNode;
  description?: string;
  children?: React.ReactNode;
  confirmText?: string;
  cancelText?: string;
  confirmLoading?: boolean;
  onConfirm?: () => void;
  onCancel?: () => void;
  disableConfirm?: boolean;
  disableCancel?: boolean;
  variant?: "primary" | "danger";
  maxWidth?: "md" | "lg" | "xl" | "2xl" | "3xl" | "4xl" | "5xl" | "6xl";
  showFooter?: boolean;
  footer?: React.ReactNode;
  fullscreen?: boolean;
};

export function Modal({
  open,
  title,
  description,
  children,
  confirmText = "Confirm",
  cancelText = "Cancel",
  confirmLoading = false,
  onConfirm,
  onCancel,
  disableConfirm = false,
  disableCancel = false,
  variant = "primary",
  maxWidth = "2xl",
  showFooter = true,
  footer,
  fullscreen = false,
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const titleId = useId();

  // Lock scroll while open
  useEffect(() => {
    if (!open) {
      document.documentElement.style.overflow = "";
      document.body.style.overflow = "";
      return () => {
        document.documentElement.style.overflow = "";
        document.body.style.overflow = "";
      };
    }

    const htmlStyle = document.documentElement.style;
    const bodyStyle = document.body.style;
    const prevHtmlOverflow = htmlStyle.overflow || "";
    const prevBodyOverflow = bodyStyle.overflow || "";

    htmlStyle.overflow = "hidden";
    bodyStyle.overflow = "hidden";

    return () => {
      htmlStyle.overflow = prevHtmlOverflow;
      bodyStyle.overflow = prevBodyOverflow;
    };
  }, [open]);

  // Auto-focus dialog on open
  useEffect(() => {
    if (open && dialogRef.current) {
      // Small delay to let the animation start before focusing
      const timer = setTimeout(() => {
        dialogRef.current?.focus();
      }, 50);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [open]);

  const handleKeyDown = useEffectEvent((e: KeyboardEvent) => {
    if (e.key === "Escape") {
      onCancel?.();
    }
  });

  useEffect(() => {
    if (!open) {
      return () => {
        document.removeEventListener("keydown", handleKeyDown);
      };
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onCancel]);

  // Focus trap
  const handleFocusTrap = useCallback((e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !dialogRef.current) return;

    const focusable =
      dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }, []);

  if (!open) return null;

  const maxWidthClass = fullscreen
    ? "max-w-none h-full"
    : MAX_WIDTH_CLASSES[maxWidth] || "max-w-2xl";

  const isStringTitle = typeof title === "string";

  const modalContent = (
    <div
      className="fixed inset-0 z-9999 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? titleId : undefined}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-[3px] z-9998 animate-[modalFadeIn_200ms_ease-out]"
        onClick={() => onCancel?.()}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        ref={dialogRef}
        tabIndex={-1}
        onKeyDown={handleFocusTrap}
        className={cn(
          "relative z-9999 flex w-full flex-col bg-white shadow-[0_25px_50px_-12px_rgba(0,0,0,0.15),0_0_0_1px_rgba(0,0,0,0.03)] outline-none",
          "animate-[modalSlideIn_250ms_cubic-bezier(0.16,1,0.3,1)]",
          fullscreen ? "rounded-xl" : "rounded-2xl",
          maxWidthClass,
        )}
      >
        {/* Header */}
        {(title || onCancel) && (
          <div className="flex items-start gap-4 px-6 pt-5 pb-4">
            <div className="flex-1 min-w-0" id={titleId}>
              {isStringTitle ? (
                <h2 className="text-lg font-semibold leading-snug text-slate-900">
                  {title}
                </h2>
              ) : (
                title
              )}
              {description && (
                <p className="mt-1 text-sm text-slate-500">{description}</p>
              )}
            </div>
            {onCancel && (
              <button
                type="button"
                aria-label="Close"
                onClick={() => onCancel?.()}
                className="shrink-0 -mr-1 -mt-1 inline-flex size-8 cursor-pointer items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
        )}

        {/* Divider under header */}
        {(title || onCancel) && (
          <div className="mx-6 border-t border-slate-100" />
        )}

        {/* Body */}
        {children && (
          <div
            className={cn(
              "px-6 py-5",
              fullscreen
                ? "flex-1 min-h-0 flex flex-col overflow-hidden"
                : "flex-1 max-h-[65vh] overflow-y-auto overflow-x-auto",
            )}
          >
            {children}
          </div>
        )}

        {/* Footer */}
        {footer ? (
          <div className="border-t border-slate-100 px-6 py-3.5 rounded-b-2xl bg-slate-50/50">
            {footer}
          </div>
        ) : showFooter ? (
          <div className="flex items-center justify-end gap-2.5 border-t border-slate-100 px-6 py-3.5 rounded-b-2xl bg-slate-50/50">
            <button
              type="button"
              onClick={() => onCancel?.()}
              disabled={disableCancel || confirmLoading}
              className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {cancelText}
            </button>
            <button
              type="button"
              onClick={() => onConfirm?.()}
              disabled={disableConfirm || confirmLoading}
              className={cn(
                "inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed",
                variant === "danger"
                  ? "bg-rose-600 hover:bg-rose-700 focus-visible:ring-rose-500"
                  : "bg-indigo-600 hover:bg-indigo-700 focus-visible:ring-indigo-500",
              )}
              aria-busy={confirmLoading}
            >
              {confirmLoading && (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              )}
              {confirmLoading ? "Processing..." : confirmText}
            </button>
          </div>
        ) : null}
      </div>

      {/* Keyframe styles */}
      <style>{`
        @keyframes modalFadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes modalSlideIn {
          from {
            opacity: 0;
            transform: scale(0.97) translateY(6px);
          }
          to {
            opacity: 1;
            transform: scale(1) translateY(0);
          }
        }
      `}</style>
    </div>
  );

  return createPortal(modalContent, document.body);
}
