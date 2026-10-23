"use client";

import type { ReactNode } from "react";

import { COLLECTION_CARD_HEIGHT, COLLECTION_GRID } from "@/lib/constants";
import { cn } from "@/platform/utils";

/**
 * The board while the collections are still in flight.
 *
 * An outline of what is coming rather than a line of text saying it is coming:
 * the board is a grid of fixed-height cards, so a spinner or a sentence would
 * collapse the page to nothing and then push everything down when the list
 * lands. Every measurement here — the grid track, the card height, the mark,
 * the name, the pill row, the action bar — is `CollectionCard`'s own, so the
 * real cards drop into the same boxes and nothing moves.
 *
 * Drawn in this product's tokens rather than with the agent UI's shared
 * `Skeleton` kit: that kit is painted in literal slate and white, which reads
 * as a light-mode card sitting in a dark-mode board. `animate-pulse` on
 * `bg-sunken` is the idiom the rest of this product already loads with.
 */

/** One shimmering block. `delay` staggers the row so it does not pulse as one. */
function Block({
  className,
  delay = 0,
}: {
  className?: string;
  delay?: number;
}) {
  return (
    <span
      // Honouring reduced motion is not decoration here: a grid of blocks
      // pulsing in unison is exactly the kind of movement that setting is for.
      // `bg-line`, not `bg-sunken`: on a card this pale, the sunken fill is
      // within a shade of the card itself and the outline all but disappears —
      // which is a loading state nobody can see.
      className={cn(
        "block animate-pulse bg-line motion-reduce:animate-none",
        className,
      )}
      style={delay ? { animationDelay: `${delay}ms` } : undefined}
    />
  );
}

function CollectionCardSkeleton({ delay = 0 }: { delay?: number }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "flex flex-col overflow-hidden rounded-di-lg",
        // Flat, where the real card is a wash: the gradient is the card's own
        // identity and printing it under placeholder blocks makes a card that
        // has not arrived look like one that has.
        "border border-line bg-surface shadow-card",
        COLLECTION_CARD_HEIGHT,
      )}
    >
      <span className="flex grow flex-col items-center overflow-hidden p-5">
        {/* The mark, the name, then the one line of counts under it. */}
        <Block className="mb-3 size-14 shrink-0 rounded-2xl" delay={delay} />
        <Block className="h-5 w-36 rounded-di-sm" delay={delay + 60} />
        <Block className="mt-2 h-3 w-44 rounded-di-sm" delay={delay + 120} />

        {/* Two pills: every card carries at least its schema, and most carry a
            second. Three would claim more than an average card shows. */}
        <span className="mt-3 flex flex-wrap justify-center gap-1.5">
          <Block className="h-5 w-24 rounded-full" delay={delay + 180} />
          <Block className="h-5 w-16 rounded-full" delay={delay + 220} />
        </span>

        <span className="mt-auto flex w-full shrink-0 items-center justify-center gap-1.5 border-t border-line-soft pt-4">
          <Block className="size-8 rounded-di-sm" delay={delay + 260} />
          <Block className="size-8 rounded-di-sm" delay={delay + 300} />
          <Block className="size-8 rounded-di-sm" delay={delay + 340} />
        </span>
      </span>
    </div>
  );
}

/**
 * The board's grid, loading.
 *
 * `leading` is rendered first and is not a skeleton: Home passes its real
 * "Create document collection" tile, because that one control needs no data and
 * taking it away while the list loads would make the only thing you can do on
 * an empty account briefly disappear.
 */
export default function CollectionGridSkeleton({
  count = 3,
  leading,
}: {
  count?: number;
  leading?: ReactNode;
}) {
  return (
    <div className={COLLECTION_GRID} role="status" aria-busy="true">
      {leading}
      {Array.from({ length: count }).map((_, index) => (
        <CollectionCardSkeleton key={index} delay={index * 90} />
      ))}
      <span className="sr-only">Loading your collections</span>
    </div>
  );
}
