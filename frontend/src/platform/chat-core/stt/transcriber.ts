import { FetchLike } from "../types/fetch";
import { normalizeLanguage, STT_API_KEY } from "./config";
import { SttError, Transcriber } from "./types";

export interface TranscriberConfig {
  /**
   * Base URL of the STT gateway, e.g. "/stt" (dashboard proxy) or
   * "https://stt.futuresmart.ai/api/stt" (direct). The "/v1/transcriptions"
   * path is appended automatically.
   */
  baseUrl: string;
  /**
   * Optional language hint passed to the backend. Either a single ISO code
   * ("en"), a pre-joined preferred list ("en,hi"), or an ordered array
   * (["en", "hi"]) — the gateway treats the first as primary, the rest as
   * acceptable fallbacks.
   */
  language?: string | readonly string[];
  /** Optional model / provider overrides understood by the gateway. */
  model?: string;
  provider?: string;
  /** Form field name for the audio file. The gateway expects "audio". */
  audioField?: string;
  /** Extra headers (e.g. Authorization, X-Trace-Id). */
  headers?: Record<string, string> | (() => Record<string, string>);
  /** Include credentials (cookies) — needed when going through the proxy. */
  withCredentials?: boolean;
  /** Injected for tests. */
  fetchImpl?: FetchLike;
}

function fileExtensionFor(mime: string): string {
  if (mime.includes("webm")) return "webm";
  if (mime.includes("ogg")) return "ogg";
  if (mime.includes("mp4") || mime.includes("mpeg")) return "mp4";
  if (mime.includes("wav")) return "wav";
  return "webm";
}

function readText(payload: unknown): string {
  if (typeof payload === "string") return payload.trim();
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    const candidate = obj.text ?? obj.transcript ?? obj.transcription;
    if (typeof candidate === "string") return candidate.trim();
  }
  return "";
}

/**
 * Build a {@link Transcriber} that POSTs recorded audio to the fs-voice STT
 * gateway (`POST {baseUrl}/v1/transcriptions`, multipart/form-data) and returns
 * the recognised text. Auth is supplied via `headers` so the caller controls
 * whether the token lives server-side (proxy) or is fetched per-request.
 */
export function createTranscriber(config: TranscriberConfig): Transcriber {
  const {
    baseUrl,
    model,
    provider,
    audioField = "audio",
    headers,
    fetchImpl,
  } = config;
  const language = normalizeLanguage(config.language);

  const doFetch = fetchImpl ?? globalThis.fetch?.bind(globalThis);
  const endpoint = `${baseUrl.replace(/\/$/, "")}/v1/transcriptions`;

  return async (audio, options) => {
    if (!doFetch) {
      throw new SttError("unsupported", "fetch is not available.");
    }

    const form = new FormData();
    const ext = fileExtensionFor(audio.type || "audio/webm");
    form.append(audioField, audio, `recording.${ext}`);
    form.append("response_format", "json");
    if (language) form.append("language", language);
    if (model) form.append("model", model);
    if (provider) form.append("provider", provider);

    const resolvedHeaders =
      typeof headers === "function" ? headers() : (headers ?? {});

    let res: Response;
    try {
      res = await doFetch(endpoint, {
        method: "POST",
        body: form,
        headers: {
          ...resolvedHeaders,
          authorization: `Bearer ${STT_API_KEY}`,
        },
        signal: options?.signal,
      });
    } catch (err) {
      if ((err as Error)?.name === "AbortError") {
        throw new SttError("aborted", "Transcription was cancelled.");
      }
      throw new SttError(
        "network",
        "Could not reach the transcription service.",
      );
    }

    if (!res.ok) {
      if (res.status === 401 || res.status === 403) {
        throw new SttError(
          "network",
          "Transcription service rejected the request.",
        );
      }
      throw new SttError("network", `Transcription failed (${res.status}).`);
    }

    let payload: unknown;
    const contentType = res.headers.get("content-type") ?? "";
    try {
      payload = contentType.includes("application/json")
        ? await res.json()
        : await res.text();
    } catch {
      throw new SttError(
        "network",
        "Could not parse the transcription response.",
      );
    }

    const text = readText(payload);
    if (!text) {
      throw new SttError("no-speech", "No speech was detected. Try again.");
    }
    return text;
  };
}
