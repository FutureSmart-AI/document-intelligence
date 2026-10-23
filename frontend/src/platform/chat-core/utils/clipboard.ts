/**
 * Copies text to the clipboard using the Clipboard API.
 * Returns true on success, false on failure.
 * Toast/notification handling is left to the consumer.
 */
export const copyToClipboard = async (text: string): Promise<boolean> => {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
};
