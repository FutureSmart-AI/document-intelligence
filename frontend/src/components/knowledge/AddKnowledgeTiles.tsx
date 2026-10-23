"use client";

import Icon from "@/components/Icon";
import { cn } from "@/platform/utils";

import { GRID_COLS, TILES } from "./constants";

/** The four ways documents get in, as the artifact lays them out. */
export type IngestSource = "file" | "plain_text" | "url" | "clickup";

export default function AddKnowledgeTiles({
  onPick,
  disabled,
  compact,
}: {
  onPick: (source: IngestSource) => void;
  /** Sources the account's feature flags switch off are not offered. */
  disabled?: Partial<Record<IngestSource, boolean>>;
  /**
   * The ribbon above the one table.
   *
   * Same cards, same icons, same blurbs, laid on their side: the icon sits
   * beside the words rather than above them, which halves the height. The
   * table is what the screen is for, and stacked tiles pushed it to the middle
   * of the first viewport.
   */
  compact?: boolean;
}) {
  const tiles = TILES.filter((tile) => !disabled?.[tile.source]);
  if (!tiles.length) return null;

  return (
    <>
      <div
        className={cn(
          "flex flex-wrap items-baseline gap-2.5",
          compact ? "mb-1.75" : "mt-0.5 mb-2.5",
        )}
      >
        <b
          className={cn("text-ink", compact ? "text-[13px]" : "text-[14.5px]")}
        >
          Add knowledge
        </b>
        <span
          className={cn(
            "text-ink-2",
            compact ? "text-[11.5px]" : "text-[12.5px]",
          )}
        >
          Pick one — everything after that is automatic.
        </span>
      </div>
      <div
        data-demo="knowledge-add-tiles"
        className={cn(
          "grid",
          compact ? "mb-3.5 gap-2.5" : "mb-4.5 gap-3",
          GRID_COLS[tiles.length] ?? "sm:grid-cols-2 xl:grid-cols-4",
        )}
      >
        {tiles.map((tile) => (
          <button
            key={tile.source}
            type="button"
            onClick={() => onPick(tile.source)}
            className={cn(
              // Flat surface, a one-pixel lift on hover: the tile is a card in
              // the same material as the table below it, not a coloured banner
              // competing with it.
              "flex cursor-pointer rounded-di-lg border border-line bg-surface text-left shadow-card transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-px hover:border-accent hover:shadow-pop",
              compact
                ? "items-center gap-2.5 px-3 py-2.5"
                : "flex-col gap-1.75 p-4",
            )}
          >
            <span
              className={cn(
                "flex flex-none items-center justify-center bg-accent-soft text-accent",
                compact ? "size-8 rounded-[9px]" : "mb-0.5 size-11 rounded-xl",
              )}
            >
              <Icon
                name={tile.icon}
                className={compact ? "size-4.5" : "size-6"}
              />
            </span>
            <span className={cn("min-w-0", compact && "flex-1")}>
              <b
                className={cn(
                  "block font-bold text-ink",
                  compact ? "text-[13px]" : "text-[14px]",
                )}
              >
                {tile.title}
              </b>
              <span
                className={cn(
                  "block leading-[1.4] text-ink-2",
                  // One line at a glance in the ribbon; the full sentence is
                  // still there for anyone who widens the window or hovers.
                  compact ? "line-clamp-1 text-[11.5px]" : "text-[12px]",
                )}
                title={compact ? tile.note : undefined}
              >
                {tile.note}
              </span>
            </span>
          </button>
        ))}
      </div>
    </>
  );
}
