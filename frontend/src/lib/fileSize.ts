/**
 * A file's size in the units a person reads it in.
 *
 * Shared rather than local to one dialog: the add-documents list and the
 * sample document on the create screen print the same thing, and two copies
 * would drift the first time one of them rounded differently.
 */
export function readableSize(bytes: number): string {
  if (!bytes) return "0 bytes";
  const units = ["bytes", "KB", "MB", "GB"];
  const step = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${Number((bytes / 1024 ** step).toFixed(2))} ${units[step]}`;
}
