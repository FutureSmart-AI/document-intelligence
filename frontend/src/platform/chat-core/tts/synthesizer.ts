import { FetchLike } from "../types/fetch";
import {
  DEFAULT_FORMAT,
  DEFAULT_MODEL,
  DEFAULT_SPEED,
  DEFAULT_VOICE,
  STT_API_KEY,
} from "./config";
import { Synthesizer, TtsError } from "./types";

export interface SynthesizerConfig {
  /** Gateway base, e.g. "https://stt.futuresmart.ai/api/stt". `/v1/speech` is appended. */
  baseUrl: string;
  voice?: string;
  model?: string;
  speed?: number;
  outputFormat?: "mp3" | "wav" | "opus";
  /** Extra headers (e.g. X-Trace-Id). */
  headers?: Record<string, string> | (() => Record<string, string>);
  /** Send cookies — needed when routing through a same-origin proxy. */
  withCredentials?: boolean;
  /** Injected for tests. */
  fetchImpl?: FetchLike;
}

/**
 * Build a {@link Synthesizer} that POSTs text to the fs-voice gateway
 * (`POST {baseUrl}/v1/speech`, JSON) and returns a playable audio blob URL.
 */
export function createSynthesizer(config: SynthesizerConfig): Synthesizer {
  const {
    baseUrl,
    voice = DEFAULT_VOICE,
    model = DEFAULT_MODEL,
    speed = DEFAULT_SPEED,
    outputFormat = DEFAULT_FORMAT,
    headers,
    withCredentials,
    fetchImpl,
  } = config;

  const doFetch = fetchImpl ?? globalThis.fetch?.bind(globalThis);
  const endpoint = `${baseUrl.replace(/\/$/, "")}/v1/speech`;

  return async (text, options) => {
    const trimmed = (text ?? "").trim();
    if (!trimmed) throw new TtsError("empty-text", "Nothing to read aloud.");
    if (!doFetch) throw new TtsError("unsupported", "fetch is not available.");

    const resolvedHeaders =
      typeof headers === "function" ? headers() : (headers ?? {});

    let res: Response;
    try {
      res = await doFetch(endpoint, {
        method: "POST",
        headers: {
          ...resolvedHeaders,
          "content-type": "application/json",
          authorization: `Bearer ${STT_API_KEY}`,
        },
        body: JSON.stringify({
          text: trimmed,
          voice,
          model,
          output_format: outputFormat,
          speed,
        }),
        credentials: withCredentials ? "include" : undefined,
        signal: options?.signal,
      });
    } catch (err) {
      if ((err as Error)?.name === "AbortError") {
        throw new TtsError("aborted", "Playback was cancelled.");
      }
      throw new TtsError("network", "Could not reach the speech service.");
    }

    if (!res.ok) {
      if (res.status === 429) {
        throw new TtsError(
          "rate-limited",
          "Too many requests. Try again in a moment.",
        );
      }
      throw new TtsError("network", `Speech synthesis failed (${res.status}).`);
    }

    const blob = await res.blob();
    const header = res.headers.get("x-estimated-duration-seconds");
    const parsed = header ? parseFloat(header) : NaN;
    return {
      blob,
      url: URL.createObjectURL(blob),
      durationSeconds: Number.isFinite(parsed) ? parsed : null,
      mimeType: blob.type || "audio/mpeg",
    };
  };
}
