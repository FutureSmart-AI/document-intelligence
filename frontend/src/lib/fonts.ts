import localFont from "next/font/local";

/**
 * The three faces the FutureSmart products are drawn in — the same set the
 * NL2SQL dashboard carries, so a user moving between the two reads one design.
 *
 * `AppShell` puts `DOC_FONT_CLASS` on its root and the root layout puts it on
 * `<body>`, so every screen has the `--font-di-*` variables in scope.
 *
 * The variables are consumed by `../styles/theme.css`, which is also where the
 * fallback stacks live.
 *
 * The files are self-hosted from `src/fonts/` (see its README) instead of
 * coming from `next/font/google`: that loader resolves gstatic URLs at build
 * time and Google intermittently answers with a form Turbopack cannot parse,
 * which is what broke a prod deploy. `next/font/local` takes a path relative
 * to this file, not a tsconfig alias.
 *
 * Each face is the full variable weight axis, so the discrete weights these
 * used to request are still rendered; the browser instances them from one file.
 */

/** Headings and title-shaped text. */
const display = localFont({
  src: "../fonts/PlusJakartaSans-Variable-latin.woff2",
  weight: "200 800",
  style: "normal",
  variable: "--font-di-display",
  display: "swap",
});

/** Body copy, controls and everything not a heading. */
const body = localFont({
  src: "../fonts/Manrope-Variable-latin.woff2",
  weight: "200 800",
  style: "normal",
  variable: "--font-di-body",
  display: "swap",
});

/** Ids, field keys, extracted values in a table. */
const mono = localFont({
  src: "../fonts/JetBrainsMono-Variable-latin.woff2",
  weight: "100 800",
  style: "normal",
  variable: "--font-di-mono",
  display: "swap",
});

/** Publishes all three font variables on whatever element carries it. */
export const DOC_FONT_CLASS = `${display.variable} ${body.variable} ${mono.variable}`;
