/**
 * Voice gateway credential for the optional mic and read-aloud.
 *
 * The browser calls the gateway directly (`sttUrl` in `@/platform/config`), and
 * the gateway needs this Bearer on both the batch (`/v1/transcriptions`) and
 * realtime (`/v1/realtime/sessions`) paths. It is a *public* gateway key — it
 * ends up in the client bundle either way — but it is configuration, not code:
 * it comes from `NEXT_PUBLIC_VOICE_GATEWAY_KEY` at build time and is never
 * committed. Unset, `voiceGatewayConfigured` is false and both voice controls
 * stay hidden instead of failing on click.
 */
export const STT_API_KEY = process.env.NEXT_PUBLIC_VOICE_GATEWAY_KEY ?? "";

/**
 * Default ordered preferred-language list sent to the gateway when an agent has
 * not configured its own language. The gateway treats the FIRST entry as the
 * primary (forced/hinted) language and the rest as acceptable fallbacks, e.g.
 * "en,hi" → English primary, Hindi accepted.
 */
export const DEFAULT_PREFERRED_LANGUAGES = ["en", "hi"] as const;

/**
 * Normalise a language hint to the gateway's comma-separated form. Accepts a
 * single code ("en"), a pre-joined string ("en,hi"), or an ordered list
 * (["en", "hi"]). Blank entries are dropped; returns undefined when nothing
 * usable remains, so the caller omits the field and the gateway auto-detects.
 */
export function normalizeLanguage(
  language?: string | readonly string[],
): string | undefined {
  if (language == null) return undefined;
  const list = typeof language === "string" ? language.split(",") : language;
  const cleaned = list.map((code) => code.trim()).filter(Boolean);
  return cleaned.length ? cleaned.join(",") : undefined;
}
