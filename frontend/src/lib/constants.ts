/** Product wordmark. Rendered as "Document **Intelligence**" by `Brand`. */
export const PRODUCT_NAME = "Document Intelligence";

/** Height of the fixed topbar, in pixels — the shell offsets content by it. */
export const TOPBAR_HEIGHT = 56;

/**
 * Width of the collection navigation rail, in pixels — the width it opens at
 * before anyone drags it somewhere else.
 *
 * These two are the agent UI's own sidebar numbers, and the rail stores its
 * dragged width under the agent UI's key, so the rail is the same width in
 * every product a person moves between rather than jumping on each hop.
 */
export const RAIL_WIDTH = 240;

/** Width of the rail once collapsed — icons only, labels dropped. */
export const RAIL_COLLAPSED_WIDTH = 72;

/** Drag limits for the expanded rail, as in the agent UI. */
export const RAIL_MIN_WIDTH = 200;
export const RAIL_MAX_WIDTH = 400;

/**
 * Every card on the board is the same height, so a collection with several
 * pills cannot grow taller than one with none. Spelled as a class because it is
 * shared by the collection cards and the create tile that sits among them.
 * Same height and track sizing as the NL2SQL board, so the two products' boards
 * line up card for card.
 */
export const COLLECTION_CARD_HEIGHT = "h-[280px]";

/** The board grid itself — the NL2SQL dashboard's track sizing. */
export const COLLECTION_GRID =
  "grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4";

/**
 * How a website is read: the one page it was given, or the pages it links to.
 *
 * `scraping_method` is a strict enum on the knowledge service and its values are
 * lowercase even where the control displays "Scrape", so the wire value and the
 * label are kept apart here — sending `Scrape` is a 422.
 *
 * This is the only question a website asks. There is no scraper control
 * (`scrape_using` is deprecated and has no effect) and no processing-type
 * control either: the service detects how a page or a file has to be read —
 * scanned, layout-heavy, visual — and re-reads an image-only PDF by itself, so
 * there is nothing left for anyone to pick wrong. See `data/ingest`.
 */
export const SCRAPE_METHODS = [
  {
    value: "scrape",
    label: "Scrape (this page only)",
  },
  {
    value: "crawl",
    label: "Crawl (follow links)",
  },
] as const;

export type ScrapeMethod = (typeof SCRAPE_METHODS)[number]["value"];

/**
 * How far a crawl follows links, sent as `max_depth`.
 *
 * Only ever sent with `scraping_method: "crawl"` — a scrape reads the one page
 * it was given, so a depth alongside it means nothing. Two levels is as far as
 * this product offers: each level multiplies the pages fetched, and the service
 * defaults to 1 when the field is left out.
 */
export const MAX_DEPTH_OPTIONS = [
  { value: 1, label: "1 — the pages this page links to" },
  { value: 2, label: "2 — and the pages those link to" },
] as const;

export type MaxDepth = (typeof MAX_DEPTH_OPTIONS)[number]["value"];

/** What the service does when `max_depth` is omitted. */
export const DEFAULT_MAX_DEPTH: MaxDepth = 1;

/**
 * Where the viewer's theme choice is remembered.
 *
 * Named once because two very different things read it: the toggle, and the
 * blocking script in the document head that stamps the choice before first
 * paint. A key that drifts between those two is a page that renders in one
 * theme and then jumps to the other.
 */
export const THEME_STORAGE_KEY = "di-theme";

/**
 * The blocking script that applies the remembered theme before first paint.
 *
 * Inline and tiny on purpose: it has to run before the document is painted, so
 * it cannot be a module. Absent a stored choice it stamps nothing at all, which
 * is what leaves `prefers-color-scheme` in charge — see `globals.css`.
 */
export const THEME_BOOTSTRAP = `try{var t=localStorage.getItem("${THEME_STORAGE_KEY}");if(t==="dark"||t==="light"){document.documentElement.setAttribute("data-theme",t);document.documentElement.style.colorScheme=t;}}catch(e){}`;
