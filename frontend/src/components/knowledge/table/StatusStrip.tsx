"use client";

import { cn } from "@/platform/utils";

import { STATUS_TILES } from "./constants";
import type { StatusCounts } from "./types";

/**
 * Status, as the filter it always was.
 *
 * These four counts used to be a band of tiles above the toolbar — a fifth of
 * the page height spent restating what a filter dropdown already said. One
 * segmented control inside the bar keeps the counts and the click-to-filter and
 * reads as a control rather than as a dashboard.
 */
export default function StatusStrip({
  counts,
  value,
  onChange,
}: {
  counts: StatusCounts;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div
      data-demo="knowledge-status"
      className="inline-flex rounded-di-md border border-line bg-sunken p-0.5"
      role="group"
      aria-label="Filter by processing status"
    >
      {STATUS_TILES.map((tile) => {
        const active = value === tile.key;
        return (
          <button
            key={tile.key}
            type="button"
            data-demo={`knowledge-status-${tile.key}`}
            title={tile.title}
            aria-pressed={active}
            // Clicking the active bucket clears it; All is already "no filter".
            onClick={() =>
              onChange(active && tile.key !== "all" ? "all" : tile.key)
            }
            className={cn(
              "inline-flex cursor-pointer items-center gap-1.5 rounded-[7px] border px-2.5 py-[5px]",
              "text-[12.5px] font-semibold whitespace-nowrap transition-colors",
              active
                ? "border-line bg-surface text-ink shadow-card"
                : "border-transparent text-ink-2 hover:bg-surface hover:text-ink",
            )}
          >
            {tile.dot && (
              <span
                className={cn("size-1.75 flex-none rounded-full", tile.dot)}
              />
            )}
            <span>{tile.label}</span>
            <span
              className={cn(
                "tnum text-[12.5px] leading-none font-bold",
                active && tile.activeText ? tile.activeText : "text-ink",
              )}
            >
              {counts[tile.key]}
            </span>
          </button>
        );
      })}
    </div>
  );
}
