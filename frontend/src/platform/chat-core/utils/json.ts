/**
 * Safely parses a JSON string, handling double-stringified JSON and edge cases.
 * Returns null if parsing fails or input is not valid JSON.
 */
export const safeParseJson = (text: any): any => {
  if (!text || typeof text !== "string") return null;
  const trimmed = text.trim();
  if (!trimmed) return null;
  // Only attempt to parse JSON-like payloads
  if (!(trimmed.startsWith("{") || trimmed.startsWith("["))) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    try {
      // attempt to unquote double-stringified JSON
      return JSON.parse(trimmed.slice(1, -1));
    } catch {
      return null;
    }
  }
};
