import type { IconName } from "@/components/Icon";
import type { GrantableRole } from "@/data/people";

/** The languages the API card offers a sample in. */
export const API_LANGS: { key: ApiLang; label: string }[] = [
  { key: "py", label: "Python" },
  { key: "js", label: "JavaScript" },
  { key: "curl", label: "cURL" },
];

export type ApiLang = "py" | "js" | "curl";

/** What each grantable role may do, in the words the People card uses. */
export const ROLE_NOTE: Record<GrantableRole, string> = {
  editor: "Can add documents and change settings",
  viewer: "Can ask questions and read answers",
};

/**
 * Brand steps for the shared chat's header, launcher and send button.
 *
 * A closed set rather than a free colour picker: the stored value paints a
 * surface that carries white text, and an arbitrary colour there can land
 * unreadable. Each of these does not.
 */
export const BRAND_SWATCHES: { hex: string; label: string }[] = [
  { hex: "#2563eb", label: "FutureSmart blue" },
  { hex: "#0f9488", label: "Teal" },
  { hex: "#7c3aed", label: "Violet" },
  { hex: "#b87c15", label: "Amber" },
  { hex: "#111827", label: "Ink" },
];

/**
 * What an icon choice shows when its own asset cannot be painted.
 *
 * The service's defaults are stored as `binary/octet-stream` and are recovered
 * over CORS (see `RemoteAsset`); when even that fails the choice still needs to
 * look like something, so it borrows this product's own sprite — in the order
 * the artifact draws its launcher choices, so the row reads the same way.
 */
export const FALLBACK_GLYPHS: IconName[] = [
  "chat",
  "file",
  "spark",
  "bolt",
  "users",
  "globe",
  "book",
];

/**
 * Share row disclosure motion. The open leg is longer than the close: a row
 * opening is content you are about to read, a row closing is out of the way.
 */
export const ROW_OPEN_MS = 260;
export const ROW_CLOSE_MS = 200;
export const ROW_EASE = "cubic-bezier(.22,.61,.36,1)";

/**
 * Every disclosure row on the Share screen, in the order they are worked
 * through — the ways out, then who gets in, then how it looks.
 */
export const SHARE_ROWS = [
  "api",
  "widget",
  "fullpage",
  "people",
  "appearance",
  "preview",
] as const;

export type ShareRowKey = (typeof SHARE_ROWS)[number];

/**
 * Rows per Activity page.
 *
 * The route's own default, stated here because the footer's "page N of M" and
 * the Previous/Next pair are only truthful if the page they describe is the
 * page that was asked for.
 */
export const ACTIVITY_PAGE_SIZE = 20;
