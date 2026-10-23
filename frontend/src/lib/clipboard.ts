/**
 * Copies text, resolving to whether it worked.
 *
 * `navigator.clipboard` rejects when the page is not a secure context or the
 * permission is denied, and an uncaught rejection there surfaces as a page
 * error — so every copy in this product goes through here and reports the
 * failure to its caller, which is what turns it into a toast rather than a
 * silent no-op.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard?.writeText(text);
    return true;
  } catch {
    return false;
  }
}
