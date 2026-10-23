/**
 * Strip markdown *syntax* so the TTS voice doesn't read markers aloud
 * (e.g. "asterisk asterisk bold"). Everything else — symbols (#, @, /, %, &),
 * dates, table content, URLs — is left exactly as written; the speech service
 * is responsible for pronouncing those. Intentionally lightweight: it removes
 * the common markers our agent responses use, not a full parser.
 */

/**
 * Bare http(s) URLs can contain markdown-significant characters (`_`, `*`)
 * that the emphasis rules below would mangle. Pull them out before stripping
 * and splice them back verbatim at the end so the spoken text keeps the exact
 * link. URLs inside markdown links/images are still removed downstream — the
 * placeholder sits where the URL was and the `[text](…)` / `![alt](…)` rules
 * match it just the same.
 */
const URL_PATTERN = /https?:\/\/[^\s)]+/gi;
// NUL surrounds the index: survives every rewrite below (not whitespace, not a
// markdown char, never produced by the agent's text).
const URL_SENTINEL = "\u0000";

export function toPlainText(markdown: string): string {
  if (!markdown) return "";
  const urls: string[] = [];
  const masked = markdown.replace(URL_PATTERN, (url) => {
    const token = `${URL_SENTINEL}${urls.length}${URL_SENTINEL}`;
    urls.push(url);
    return token;
  });
  const restore = (text: string): string =>
    text.replace(
      new RegExp(`${URL_SENTINEL}(\\d+)${URL_SENTINEL}`, "g"),
      (_, i) => urls[Number(i)] ?? "",
    );
  return restore(
    masked
      .replace(/```[\s\S]*?```/g, " ") // fenced code blocks
      .replace(/`([^`]+)`/g, "$1") // inline code
      .replace(/!\[[^\]]*\]\([^)]*\)/g, " ") // images
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // links -> text
      .replace(/^#{1,6}\s+/gm, "") // headings
      .replace(/^\s*>+\s?/gm, "") // blockquotes
      .replace(/^\s*[-*+]\s+/gm, "") // unordered list markers
      .replace(/^\s*\d+\.\s+/gm, "") // ordered list markers
      .replace(/(\*\*|__)(.*?)\1/g, "$2") // bold
      .replace(/(\*|_)(.*?)\1/g, "$2") // italic
      .replace(/~~(.*?)~~/g, "$2") // strikethrough
      .replace(/\s+/g, " ") // collapse whitespace
      .trim(),
  );
}
