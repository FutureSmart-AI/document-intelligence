/**
 * Conditionally join class names, filtering out falsy values.
 * Framework-agnostic (no tailwind-merge) so chat-core stays dependency-light;
 * consumers that need conflict resolution can pass already-merged strings.
 */
export const cn = (...classes: (string | undefined | null | false)[]): string =>
  classes.filter(Boolean).join(" ");
