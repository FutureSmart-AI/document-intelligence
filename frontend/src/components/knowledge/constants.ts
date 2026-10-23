import type { IconName } from "@/components/Icon";

import type { IngestSource } from "./AddKnowledgeTiles";

/**
 * The ways a document gets into a collection, and how the strip lays them out.
 *
 * Colocated here rather than at the top of the component that draws them, per
 * the repo's `no-restricted-syntax` rule: which sources are offered, and what
 * each one promises, is a product decision someone will want to change without
 * reading JSX.
 */

export const TILES: Array<{
  source: IngestSource;
  icon: IconName;
  title: string;
  note: string;
}> = [
  {
    source: "file",
    icon: "up",
    title: "Files",
    note: "PDF, Word, spreadsheets, images. Drag a folder in and every file becomes a document.",
  },
  {
    source: "plain_text",
    icon: "file",
    title: "Paste text",
    note: "Paste text straight in — notes, an email thread, anything you have as text.",
  },
  {
    source: "url",
    icon: "globe",
    title: "Website",
    note: "Give a URL and we read the page. Crawl a whole site section if you want.",
  },
  {
    source: "clickup",
    icon: "link",
    title: "Sources & sync",
    note: "Connect a live source like ClickUp. New and changed pages keep flowing in.",
  },
];

/** Tiles share the row evenly, so the count decides the column split. */
export const GRID_COLS: Record<number, string> = {
  1: "grid-cols-1",
  2: "sm:grid-cols-2",
  3: "sm:grid-cols-2 lg:grid-cols-3",
  4: "sm:grid-cols-2 xl:grid-cols-4",
};
