import type { ReactNode } from "react";

/**
 * The shared kit's own values: how a button, a pill and a form control are
 * painted.
 *
 * Colocated here rather than at the top of `ui.tsx`, per the repo's
 * `no-restricted-syntax` rule. They are class lists, so they are the definition
 * of what this product's controls look like — the thing most likely to be
 * changed without wanting to read the components that use them.
 */

export type ButtonVariant = "default" | "primary" | "ghost" | "dangerGhost";
export type PillTone =
  "count" | "success" | "warn" | "danger" | "neutral" | "accent";

export const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  default:
    "border border-line-strong bg-surface text-ink hover:bg-sunken disabled:opacity-50",
  primary:
    "border border-accent bg-accent text-accent-contrast hover:bg-accent-strong hover:border-accent-strong disabled:opacity-50",
  ghost: "border border-transparent text-ink hover:bg-sunken",
  dangerGhost:
    "border border-transparent text-danger-text hover:bg-danger-bg disabled:opacity-50",
};

export const PILL_TONES: Record<PillTone, string> = {
  count: "bg-accent-soft text-accent-softtext",
  accent: "bg-accent-soft text-accent",
  success: "bg-success-bg text-success-text",
  warn: "bg-warn-bg text-warn-text",
  danger: "bg-danger-bg text-danger-text",
  neutral: "border border-line bg-sunken text-ink-2",
};

export const CONTROL =
  "w-full rounded-di-md border border-line-strong bg-surface px-2.5 py-2 text-[13.5px] text-ink outline-none focus-visible:border-accent";

/** Every glyph this product draws, keyed by the name components ask for. */
export const ICONS = {
  home: (
    <>
      <path d="M3 8.6L10 3l7 5.6V17H3z" />
      <path d="M8 17v-5h4v5" />
    </>
  ),
  file: (
    <>
      <path d="M11.4 3H5.4v14h9.2V6.2z" />
      <path d="M11.4 3v3.2h3.2" />
    </>
  ),
  book: (
    <>
      <path d="M3.4 4.2h4.2c1.3 0 2.4.9 2.4 2v9.6c0-.9-1-1.6-2.2-1.6H3.4z" />
      <path d="M16.6 4.2h-4.2c-1.3 0-2.4.9-2.4 2v9.6c0-.9 1-1.6 2.2-1.6h4.4z" />
    </>
  ),
  /* Subject marks. A collection wears the one that matches what it holds, so
   * the board is scannable before a name is read — the artifact's own boards
   * never repeat one document glyph across every card. Each is the sprite's
   * document plate with its subject drawn inside, so they read as one family. */
  /** Resumes and CVs — a document with a person on it. */
  docPerson: (
    <>
      <path d="M11.4 3H5.4v14h9.2V6.2z" />
      <path d="M11.4 3v3.2h3.2" />
      <circle cx="10" cy="10.4" r="1.5" />
      <path d="M7.6 14.6c0-1.3 1.1-2.3 2.4-2.3s2.4 1 2.4 2.3" />
    </>
  ),
  /** Contracts and agreements — a document with a signature on it. */
  docSigned: (
    <>
      <path d="M11.4 3H5.4v14h9.2V6.2z" />
      <path d="M11.4 3v3.2h3.2" />
      <path d="M7.4 9.4h3.4" />
      <path d="M7.4 13.6c1.2-1.8 2-1.8 2.6-.8s1.4.8 2.6-1" />
    </>
  ),
  /** Invoices, receipts, purchase orders — a document with a total on it. */
  invoice: (
    <>
      <path d="M11.4 3H5.4v14h9.2V6.2z" />
      <path d="M11.4 3v3.2h3.2" />
      <path d="M7.4 9.6h2.8M7.4 11.8h4.8M9.6 14h2.6" />
    </>
  ),
  /** Support tickets — the artifact's stub with its perforation. */
  ticket: (
    <>
      <path d="M3 7.2c0-.9.7-1.6 1.6-1.6h10.8c.9 0 1.6.7 1.6 1.6v1.4a1.6 1.6 0 0 0 0 2.8v1.4c0 .9-.7 1.6-1.6 1.6H4.6c-.9 0-1.6-.7-1.6-1.6v-1.4a1.6 1.6 0 0 0 0-2.8z" />
      <path d="M8.6 7.4v5.2" />
    </>
  ),
  /** Reports and analyses — a document with its chart showing. */
  docChart: (
    <>
      <path d="M11.4 3H5.4v14h9.2V6.2z" />
      <path d="M11.4 3v3.2h3.2" />
      <path d="M7.8 14v-2.4M10 14V9.6M12.2 14v-3.4" />
    </>
  ),
  /** Email threads — an envelope. */
  mail: (
    <>
      <rect x="2.8" y="5" width="14.4" height="10" rx="1.8" />
      <path d="M3.4 6.6L10 11l6.6-4.4" />
    </>
  ),

  /** Pasted text — the document mark with its lines showing. */
  text: (
    <>
      <path d="M11.4 3H5.4v14h9.2V6.2z" />
      <path d="M11.4 3v3.2h3.2" />
      <path d="M7.6 10.4h4.8M7.6 13h3.2" />
    </>
  ),
  image: (
    <>
      <rect x="3" y="4.4" width="14" height="11.2" rx="1.8" />
      <circle cx="7.6" cy="8.4" r="1.3" />
      <path d="M3.4 13.6l3.8-3.4 3 2.6 2.6-2.2 3.8 3.4" />
    </>
  ),
  /** A collection of more than one kind of document. */
  layers: (
    <>
      <path d="M10 2.6L3.2 6.2 10 9.8l6.8-3.6z" />
      <path d="M3.2 10.4L10 14l6.8-3.6" />
      <path d="M3.2 14.2L10 17.8l6.8-3.6" />
    </>
  ),
  tbl: (
    <>
      <rect x="3" y="4" width="14" height="12" rx="1.8" />
      <path d="M3 8h14M7.6 8v8" />
    </>
  ),
  compass: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M12.9 7.1l-1.5 4.3-4.3 1.5 1.5-4.3z" />
    </>
  ),
  chat: (
    <>
      <path d="M3.4 4.4h13.2v9H9l-4 3.4V13.4H3.4z" />
      <path d="M7 8.4h6M7 11h4" />
    </>
  ),
  cog: (
    <>
      <circle cx="10" cy="10" r="2.6" />
      <path d="M10 2.4v2.2M10 15.4v2.2M2.4 10h2.2M15.4 10h2.2M4.6 4.6l1.6 1.6M13.8 13.8l1.6 1.6M15.4 4.6l-1.6 1.6M6.2 13.8l-1.6 1.6" />
    </>
  ),
  search: (
    <>
      <circle cx="9" cy="9" r="5.2" />
      <path d="M12.9 12.9l4 4" />
    </>
  ),
  plus: <path d="M10 4v12M4 10h12" />,
  chk: <path d="M4 10.5l4 4L16 6" />,
  x: <path d="M5 5l10 10M15 5L5 15" />,
  trash: (
    <>
      <path d="M4.4 6.4h11.2" />
      <path d="M8.2 6.4V4.6h3.6v1.8" />
      <path d="M5.8 6.4l.7 9.4c0 .9.7 1.6 1.6 1.6h3.8c.9 0 1.6-.7 1.6-1.6l.7-9.4" />
    </>
  ),
  copy: (
    <>
      <rect x="7" y="7" width="9.6" height="9.6" rx="1.6" />
      <path d="M13.2 7V5.2c0-.9-.7-1.6-1.6-1.6H5.2c-.9 0-1.6.7-1.6 1.6v6.4c0 .9.7 1.6 1.6 1.6H7" />
    </>
  ),
  /** The refresh arc with its arrowhead. Spun by the caller while it works. */
  ref: (
    <>
      <path d="M16.4 10a6.4 6.4 0 1 1-2-4.7" />
      <path d="M16.4 3.2v3h-3" />
    </>
  ),
  /** Into the product — an upload. */
  up: <path d="M10 15V5M6 9l4-4 4 4M4 17h12" />,
  /** Out of it — an export. */
  dn: <path d="M10 3.6v10M5.8 10l4.2 4.2 4.2-4.2M3.6 17h12.8" />,
  globe: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M3 10h14" />
      <path d="M10 3c1.9 2 2.9 4.4 2.9 7s-1 5-2.9 7c-1.9-2-2.9-4.4-2.9-7s1-5 2.9-7z" />
    </>
  ),
  link: (
    <>
      <path d="M8.4 11.6a2.8 2.8 0 0 0 4 0l2.6-2.6a2.8 2.8 0 0 0-4-4l-1 1" />
      <path d="M11.6 8.4a2.8 2.8 0 0 0-4 0L5 11a2.8 2.8 0 0 0 4 4l1-1" />
    </>
  ),
  info: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 9.2v4.4M10 6.6v.1" />
    </>
  ),
  warn: (
    <>
      <path d="M10 3.2l7.2 12.6H2.8z" />
      <path d="M10 8v3.8M10 13.8v.1" />
    </>
  ),
  /** Usage — the meter's bolt, on the same grid as the rest. */
  bolt: <path d="M11.4 2.4L4.6 11.4h4.3l-.9 6.2 7.4-9.2h-4.5z" />,
  /** Credentials — a key, bit first. */
  key: (
    <>
      <circle cx="7.2" cy="12.8" r="3.2" />
      <path d="M9.5 10.5l6.1-6.1M13.2 6.8l1.8 1.8M11.7 8.3l1.8 1.8" />
    </>
  ),
  /** Read-only — a shut padlock. */
  lock: (
    <>
      <rect x="4.5" y="9" width="11" height="7.5" rx="1.6" />
      <path d="M7.2 9V7a2.8 2.8 0 015.6 0v2" />
    </>
  ),
  spark: (
    <path d="M10 2.6l1.9 4.1 4.5.6-3.3 3.1.9 4.5L10 12.7l-4 2.2.9-4.5L3.6 7.3l4.5-.6z" />
  ),
  sun: (
    <>
      <circle cx="10" cy="10" r="3.4" />
      <path d="M10 2.4v2M10 15.6v2M2.4 10h2M15.6 10h2M4.6 4.6l1.4 1.4M14 14l1.4 1.4M15.4 4.6L14 6M6 14l-1.4 1.4" />
    </>
  ),
  moon: <path d="M16 11.8A6.6 6.6 0 0 1 8.2 4a6.8 6.8 0 1 0 7.8 7.8z" />,

  /* Roles the three shared screens need — batch reports, analytics and
   * sharing — drawn to the same 20×20 grid and stroke language as the rest. */
  /** Batch reports — a clipboard holding a list of questions. */
  clipboard: (
    <>
      <path d="M7.4 4.2H5.8c-.9 0-1.6.7-1.6 1.6v9.6c0 .9.7 1.6 1.6 1.6h8.4c.9 0 1.6-.7 1.6-1.6V5.8c0-.9-.7-1.6-1.6-1.6h-1.6" />
      <rect x="7.4" y="2.8" width="5.2" height="2.8" rx="1" />
      <path d="M7.2 9.4h5.6M7.2 12.6h3.6" />
    </>
  ),
  /** Analytics — bars rising against an axis. */
  chart: (
    <>
      <path d="M3.4 16.6h13.2" />
      <path d="M6.2 16.6V9.8M10 16.6V5.4M13.8 16.6v-4.6" />
    </>
  ),
  /** Share — one point reaching two others. */
  share: (
    <>
      <circle cx="14.6" cy="4.8" r="2.2" />
      <circle cx="5.4" cy="10" r="2.2" />
      <circle cx="14.6" cy="15.2" r="2.2" />
      <path d="M12.7 6l-5.4 3M7.3 11l5.4 3" />
    </>
  ),
  /** Run it now. */
  play: <path d="M6.8 4.6l8 5.4-8 5.4z" />,
  /** Waiting, or a duration. */
  clock: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 5.8V10l3 1.8" />
    </>
  ),
  /**
   * One person — the reader, beside their own question.
   *
   * `users` is the pair, which reads as an audience rather than as the person
   * who asked. Drawn to the same grid as `spark`, so the two sit at the same
   * weight on either side of a turn.
   */
  user: (
    <>
      <circle cx="10" cy="7" r="3" />
      <path d="M4.6 16.4c0-3 2.4-5.4 5.4-5.4s5.4 2.4 5.4 5.4" />
    </>
  ),
  /** People — who else can reach a collection. */
  users: (
    <>
      <circle cx="8" cy="7" r="2.6" />
      <path d="M3.5 16c.6-2.6 2.4-4 4.5-4s3.9 1.4 4.5 4" />
      <circle cx="14.5" cy="7.8" r="2" />
      <path d="M14 12.2c1.4.3 2.4 1.5 2.8 3.4" />
    </>
  ),
  /** Full screen — the artifact's four corner brackets. */
  expand: <path d="M7 3H3v4M13 3h4v4M17 13v4h-4M3 13v4h4" />,
  /** The API — a snippet. */
  code: <path d="M7.5 5L4 10l3.5 5M12.5 5L16 10l-3.5 5" />,
  /** A rate, for the failure-rate card. */
  percent: (
    <>
      <circle cx="6.6" cy="6.6" r="2" />
      <circle cx="13.4" cy="13.4" r="2" />
      <path d="M15 5L5 15" />
    </>
  ),
  /** A failure count — the x in its ring. */
  xCircle: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M7.6 7.6l4.8 4.8M12.4 7.6l-4.8 4.8" />
    </>
  ),

  /* The one table's own controls: hide a column group, shape the grid, and the
   * density switch. Same 20×20 grid and stroke language as the rest. */
  eye: (
    <>
      <path d="M2.5 10s3-5 7.5-5 7.5 5 7.5 5-3 5-7.5 5-7.5-5-7.5-5z" />
      <circle cx="10" cy="10" r="2.2" />
    </>
  ),
  eyeOff: (
    <>
      <path d="M2.5 10s3-5 7.5-5 7.5 5 7.5 5-3 5-7.5 5-7.5-5-7.5-5z" />
      <path d="M4 16L16 4" />
    </>
  ),
  filter: <path d="M3 4h14l-5.5 6.5V16l-3-1.5v-4z" />,
  cols: (
    <>
      <rect x="3" y="4" width="14" height="12" rx="1.4" />
      <path d="M8 4v12" />
    </>
  ),
  sort: (
    <>
      <path d="M6 4v12M6 16l-2.5-2.5M6 16l2.5-2.5" />
      <path d="M14 16V4M14 4l-2.5 2.5M14 4l2.5 2.5" />
    </>
  ),
  rows: <path d="M3 5h14M3 10h14M3 15h14" />,

  pencil: (
    <>
      <path d="M13.4 3.6l3 3L7.6 15.4l-3.9.9.9-3.9z" />
      <path d="M11.8 5.2l3 3" />
    </>
  ),

  /* The chevrons are the sprite's back chevron, rotated and centred on the box
   * — call sites spin them with `rotate-90` / `rotate-180`, which is only true
   * to the glyph if its apex sits on the centre line. */
  caret: <polyline points="4.2,7.2 10,12.8 15.8,7.2" />,
  caretLeft: <polyline points="12.8,4.2 7.2,10 12.8,15.8" />,
  caretRight: <polyline points="7.2,4.2 12.8,10 7.2,15.8" />,
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof ICONS;
