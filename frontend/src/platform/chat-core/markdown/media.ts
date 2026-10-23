/**
 * Media-reference detection for chat markdown.
 *
 * The agent contract is: respond in pure markdown and express rich media as a
 * bare URL on its own line (never raw HTML). With `remark-gfm`, a bare URL on
 * its own line is autolinked, so it reaches the renderer as a paragraph whose
 * only child is `<a href={url}>{url}</a>`. We detect that shape and let the UI
 * swap in a trusted embed component — model output is never turned into an
 * iframe, so there is no model-supplied embed markup to sanitize.
 */

export type MediaMatch =
  | { type: "youtube"; id: string; url: string }
  | { type: "video"; url: string }
  | { type: "audio"; url: string };

/** YouTube video IDs are always exactly 11 url-safe base64 characters. */
const YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/;
const VIDEO_EXT = /\.(mp4|webm|ogv|mov|m4v)$/i;
const AUDIO_EXT = /\.(mp3|wav|m4a|aac|oga|ogg|flac)$/i;

/** Parse a URL, returning it only if it is a safe http(s) absolute URL. */
function parseHttpUrl(raw: string): URL | null {
  try {
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url;
  } catch {
    return null;
  }
}

/**
 * Extract and validate an 11-char YouTube video ID from any of the common
 * YouTube URL shapes (watch, youtu.be, embed, shorts, live). Returns null for
 * anything that is not a valid YouTube URL.
 */
export function extractYouTubeId(raw: string): string | null {
  const url = parseHttpUrl(raw);
  if (!url) return null;

  const host = url.hostname.replace(/^(www\.|m\.)/, "").toLowerCase();
  let id: string | null = null;

  if (host === "youtu.be") {
    id = url.pathname.split("/").filter(Boolean)[0] ?? null;
  } else if (host === "youtube.com" || host === "youtube-nocookie.com") {
    if (url.pathname === "/watch") {
      id = url.searchParams.get("v");
    } else {
      const [segment, value] = url.pathname.split("/").filter(Boolean);
      if (["embed", "shorts", "live", "v"].includes(segment ?? "")) {
        id = value ?? null;
      }
    }
  }

  return id && YOUTUBE_ID.test(id) ? id : null;
}

/**
 * Classify a single URL as an embeddable media reference, or null if it is not
 * something we render as an embed (it stays a normal link).
 */
export function detectMediaUrl(raw: string): MediaMatch | null {
  const url = parseHttpUrl(raw);
  if (!url) return null;

  const youtubeId = extractYouTubeId(raw);
  if (youtubeId) return { type: "youtube", id: youtubeId, url: raw };

  const path = url.pathname.toLowerCase();
  if (VIDEO_EXT.test(path)) return { type: "video", url: raw };
  if (AUDIO_EXT.test(path)) return { type: "audio", url: raw };

  return null;
}

function isWhitespaceText(node: any): boolean {
  return node?.type === "text" && (node.value ?? "").trim() === "";
}

/**
 * Return a MediaMatch for a hast child that is a *bare* media URL — either an
 * autolinked `<a href={url}>{url}</a>` or a single-token text URL. A labeled
 * link like `[watch](url)` (visible text different from the href) returns null
 * so it stays a normal link.
 */
function getMediaMatchFromChild(child: any): MediaMatch | null {
  if (child?.type === "element" && child.tagName === "a") {
    const href = child.properties?.href;
    if (typeof href !== "string") return null;
    const text = (child.children ?? [])
      .filter((c: any) => c.type === "text")
      .map((c: any) => c.value)
      .join("")
      .trim();
    if (text !== href.trim()) return null; // labeled link → stays a link
    return detectMediaUrl(href.trim());
  }

  if (child?.type === "text") {
    const value = (child.value ?? "").trim();
    if (!value || /\s/.test(value)) return null; // must be a single token
    return detectMediaUrl(value);
  }

  return null;
}

/**
 * Given a hast paragraph node (as passed to the ReactMarkdown `p` component),
 * return a MediaMatch when the paragraph's sole content is a bare media URL.
 * Used to decide when a paragraph can be *replaced* entirely by an embed.
 */
export function getParagraphMediaMatch(node: any): MediaMatch | null {
  if (!node || !Array.isArray(node.children)) return null;
  const significant = node.children.filter((c: any) => !isWhitespaceText(c));
  if (significant.length !== 1) return null;
  return getMediaMatchFromChild(significant[0]);
}

/**
 * Collect every bare media URL in a paragraph, in order, de-duplicated by URL.
 *
 * This is the lenient pass: agents do not reliably put a media URL on its own
 * line (e.g. "Yes — https://youtu.be/ID"), so we still embed any bare media URL
 * found among a paragraph's children. The surrounding text is preserved by the
 * caller and the embeds are rendered after it.
 */
export function getParagraphMediaMatches(node: any): MediaMatch[] {
  if (!node || !Array.isArray(node.children)) return [];
  const matches: MediaMatch[] = [];
  const seen = new Set<string>();
  for (const child of node.children) {
    const match = getMediaMatchFromChild(child);
    if (match && !seen.has(match.url)) {
      seen.add(match.url);
      matches.push(match);
    }
  }
  return matches;
}
