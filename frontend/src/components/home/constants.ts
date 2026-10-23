/**
 * The board's own presentation constants.
 *
 * Colocated here rather than at the top of the component, per the repo's
 * `no-restricted-syntax` rule.
 */

/**
 * The two account cards' shared shell.
 *
 * One string because they are the same card twice — usage and credentials — and
 * two copies of a nine-class list is how one of them quietly stops matching the
 * other on a restyle.
 */
export const ACCOUNT_CARD_CLASS =
  "group flex h-full w-full gap-4 rounded-di-lg border border-line bg-surface p-4 text-left no-underline shadow-card transition-all duration-200 hover:border-accent hover:shadow-pop";
