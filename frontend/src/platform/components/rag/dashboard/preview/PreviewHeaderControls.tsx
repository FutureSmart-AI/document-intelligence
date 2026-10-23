"use client";

import { ChevronDown, ChevronUp, Maximize2, Minimize2, X } from "lucide-react";

type PreviewHeaderControlsProps = {
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
  /** e.g. "3 of 121" — shown between the prev/next controls. */
  positionLabel?: string;
  expanded?: boolean;
  onToggleExpanded?: () => void;
};

const iconButton =
  "inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent";

/** Prev / next / expand / close cluster shared by the Knowledge and Data View
 * preview panels, so both windows behave identically. */
export const PreviewHeaderControls = ({
  onClose,
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
  positionLabel,
  expanded = false,
  onToggleExpanded,
}: PreviewHeaderControlsProps) => (
  <div className="flex shrink-0 items-center gap-1">
    {onPrev && onNext && (
      <>
        <button
          type="button"
          aria-label="Previous document"
          title="Previous document"
          className={iconButton}
          disabled={!hasPrev}
          onClick={onPrev}
        >
          <ChevronUp className="size-4" />
        </button>
        {positionLabel && (
          <span className="whitespace-nowrap px-1 text-xs text-slate-500">
            {positionLabel}
          </span>
        )}
        <button
          type="button"
          aria-label="Next document"
          title="Next document"
          className={iconButton}
          disabled={!hasNext}
          onClick={onNext}
        >
          <ChevronDown className="size-4" />
        </button>
      </>
    )}
    {onToggleExpanded && (
      <button
        type="button"
        aria-label={expanded ? "Collapse preview" : "Expand preview"}
        title={expanded ? "Collapse preview" : "Expand preview"}
        aria-pressed={expanded}
        className={iconButton}
        onClick={onToggleExpanded}
      >
        {expanded ? (
          <Minimize2 className="size-4" />
        ) : (
          <Maximize2 className="size-4" />
        )}
      </button>
    )}
    <button
      type="button"
      aria-label="Close preview"
      title="Close preview"
      className={iconButton}
      onClick={onClose}
    >
      <X className="size-4" />
    </button>
  </div>
);
