/**
 * Decodes common escape sequences and Unicode escape sequences in a string.
 * Used to process streaming text chunks that contain escaped characters.
 */
export const decodeEscapeChars = (text: string): string => {
  if (!text || typeof text !== "string") return text;

  try {
    let decoded = text;

    // First, decode common escape sequences
    decoded = decoded
      .replace(/\\n/g, "\n") // newlines
      .replace(/\\r/g, "\r") // carriage returns
      .replace(/\\t/g, "\t") // tabs
      .replace(/\\"/g, '"') // double quotes
      .replace(/\\'/g, "'") // single quotes
      .replace(/\\\\/g, "\\"); // backslashes (must be last)

    // Then, decode Unicode escape sequences like \u2011, \u2013, etc.
    decoded = decoded.replace(/\\u[\dA-Fa-f]{4}/g, (match) => {
      return String.fromCharCode(parseInt(match.replace(/\\u/g, ""), 16));
    });

    return decoded;
  } catch {
    return text;
  }
};
