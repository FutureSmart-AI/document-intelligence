import { FetchLike } from "../types/fetch";
import { normalizeLanguage, STT_API_KEY } from "./config";
import { buildWavHeader } from "./pcmRecorder";
import { SttError } from "./types";

/**
 * WebSocket client for the gateway's realtime STT path.
 *
 * Flow: mint an ephemeral session over HTTP, open a WebSocket carrying only the
 * session's `client_secret`, then push raw PCM16 frames. The gateway emits
 * `transcript.delta` (incremental fragments of the turn being recognised) and
 * `transcript.completed` (one per turn, closed by its own server-side VAD).
 *
 * Verified against the live gateway (2026-07-30):
 * - `input_format: "pcm16"` (mono, 24 kHz) is what makes the stream *continuous*:
 *   the server VAD segments turns on its own and no `commit` is needed. The
 *   default `"auto"` container mode only transcribes on `input_audio.commit`.
 * - `transcript.delta.delta` is an INCREMENTAL fragment ("Hello", " there", "."),
 *   not the running text — it must be accumulated.
 * - `transcript.completed` fires once PER TURN, so a session yields several
 *   finals; the full utterance is every turn joined in arrival order.
 * - `input_audio.commit` does NOT flush the tail in pcm16 mode (the audio still
 *   inside the VAD window is dropped). Appending ~0.8s of digital silence does
 *   flush it — that is what {@link RealtimeSession.finish} sends on stop.
 */

/** The only rate the gateway accepts on the realtime pcm16 path. */
export const REALTIME_SAMPLE_RATE = 24000;
/** Silence appended on finish() so the server VAD closes the last turn. */
const SILENCE_FLUSH_MS = 800;
const SILENCE_SLICE_MS = 200;
/** Max wait for the flushed tail turn before settling on what we already have. */
const DEFAULT_FINISH_TIMEOUT_MS = 4000;
/** After a turn lands post-flush, wait briefly for any further queued turns. */
const TURN_GRACE_MS = 400;
/** Below this much un-finalised audio there is no tail worth flushing. */
const MIN_TAIL_BYTES = (REALTIME_SAMPLE_RATE * 2 * 150) / 1000;

export interface RealtimeConfig {
  /** STT base URL, e.g. "/stt" (proxy) or "http://host:4100/stt". */
  baseUrl: string;
  /**
   * Optional language hint. A single ISO code ("en"), a pre-joined preferred
   * list ("en,hi"), or an ordered array (["en", "hi"]) — the gateway treats the
   * first as primary, the rest as acceptable fallbacks.
   */
  language?: string | readonly string[];
  /**
   * "pcm16" (default) streams raw 24 kHz PCM and gets continuous, VAD-segmented
   * transcripts while the user is still speaking. "auto" sends a WAV container
   * and only transcribes on commit — no live text.
   */
  inputFormat?: "pcm16" | "auto";
  headers?: Record<string, string> | (() => Record<string, string>);
  withCredentials?: boolean;
  /**
   * Connect straight to the gateway-issued `websocket_url` (the ephemeral-token
   * pattern — browser talks to the gateway directly). Requires that gateway
   * host in the host page's CSP `connect-src`. Default false: derive the WS
   * from `baseUrl` so it rides the SAME proxy origin the widget already uses —
   * the gateway's own subdomain (e.g. stt.futuresmart.ai) is otherwise blocked
   * by the customer's connect-src (see the widget's STT_URL note).
   */
  directWs?: boolean;
  /** Injected for tests / SSR safety. */
  fetchImpl?: FetchLike;
  wsImpl?: typeof WebSocket;
}

export interface RealtimeCallbacks {
  /**
   * Live transcript so far: every finalised turn plus the in-flight turn's
   * accumulated deltas. Fires on each `transcript.delta`.
   */
  onPartial?: (text: string) => void;
  /** A turn was finalised. Receives the full transcript including that turn. */
  onTurn?: (text: string) => void;
  onError: (err: SttError) => void;
}

export interface RealtimeSession {
  /** Mint + open the socket. Rejects on failure. */
  start: (sampleRate: number, cb: RealtimeCallbacks) => Promise<void>;
  /** Queue/send a PCM16 chunk (24 kHz mono on the pcm16 path). */
  sendChunk: (pcm: Uint8Array) => void;
  /**
   * Flush the audio still inside the VAD window and resolve with the full
   * transcript (all turns joined). Resolves early when nothing is outstanding.
   */
  finish: (timeoutMs?: number) => Promise<string>;
  /** Container mode only: tell the gateway the buffered audio is a turn. */
  commit: () => void;
  /** Close the socket. */
  close: () => void;
  /** Full transcript accumulated so far. */
  readonly transcript: string;
}

function toBase64(bytes: Uint8Array): string {
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(
      ...(bytes.subarray(i, i + CHUNK) as unknown as number[]),
    );
  }
  if (typeof btoa !== "undefined") return btoa(bin);
  return Buffer.from(bytes).toString("base64");
}

/** Derive the proxy WS URL from the HTTP STT base URL (fallback). */
export function realtimeWsUrl(baseUrl: string, clientSecret: string): string {
  const ref =
    typeof window !== "undefined" ? window.location.href : "http://localhost";
  const url = new URL(baseUrl, ref);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/$/, "")}/v1/realtime`;
  url.search = `?session=${encodeURIComponent(clientSecret)}`;
  return url.toString();
}

/**
 * Resolve the socket URL: prefer the gateway-issued `websocket_url` (the
 * ephemeral-token pattern — the browser connects directly, no socket proxying
 * needed), falling back to deriving it from the proxy base URL.
 */
export function resolveWsUrl(
  baseUrl: string,
  clientSecret: string,
  websocketUrl?: string,
): string {
  if (websocketUrl) {
    const sep = websocketUrl.includes("?") ? "&" : "?";
    return `${websocketUrl}${sep}session=${encodeURIComponent(clientSecret)}`;
  }
  return realtimeWsUrl(baseUrl, clientSecret);
}

/** Join finalised turns + the in-flight fragment into one readable transcript. */
export function joinTranscript(turns: string[], partial: string): string {
  return [...turns, partial]
    .map((part) => part.trim())
    .filter(Boolean)
    .join(" ");
}

export function createRealtimeSession(config: RealtimeConfig): RealtimeSession {
  const { baseUrl, headers, fetchImpl, wsImpl, directWs } = config;
  const language = normalizeLanguage(config.language);
  const inputFormat = config.inputFormat ?? "pcm16";
  const doFetch = fetchImpl ?? globalThis.fetch?.bind(globalThis);
  const WS = wsImpl ?? (globalThis as any).WebSocket;

  let ws: WebSocket | null = null;
  let open = false;
  const queue: string[] = [];

  // Transcript state. `turns` are gateway-finalised; `partial` is the deltas of
  // the turn currently being recognised.
  const turns: string[] = [];
  let partial = "";
  /** PCM bytes appended since the last finalised turn — the pending tail. */
  let pendingBytes = 0;
  /** Set while finish() waits for the flushed tail turn. */
  let awaitTail: ((text: string) => void) | null = null;

  const full = () => joinTranscript(turns, partial);

  const send = (obj: unknown) => {
    const text = JSON.stringify(obj);
    if (open && ws) ws.send(text);
    else queue.push(text);
  };

  const sendSilence = () => {
    const sliceBytes = (REALTIME_SAMPLE_RATE * 2 * SILENCE_SLICE_MS) / 1000;
    const silence = toBase64(new Uint8Array(sliceBytes));
    const slices = Math.ceil(SILENCE_FLUSH_MS / SILENCE_SLICE_MS);
    for (let i = 0; i < slices; i++) {
      send({ type: "input_audio.append", audio: silence });
    }
  };

  return {
    get transcript() {
      return full();
    },

    async start(sampleRate, cb) {
      if (!doFetch) throw new SttError("unsupported", "fetch is unavailable.");
      if (!WS) throw new SttError("unsupported", "WebSocket is unavailable.");

      const resolvedHeaders =
        typeof headers === "function" ? headers() : (headers ?? {});

      // 1. Mint an ephemeral session. The Bearer key is sent directly to the
      // gateway (the browser calls the gateway, not a proxy — see STT_API_KEY).
      let secret: string;
      let wsUrlFromMint: string | undefined;
      try {
        const res = await doFetch(
          `${baseUrl.replace(/\/$/, "")}/v1/realtime/sessions`,
          {
            method: "POST",
            headers: {
              ...resolvedHeaders,
              authorization: `Bearer ${STT_API_KEY}`,
              "content-type": "application/json",
            },
            body: JSON.stringify({
              mode: "stt",
              language,
              input_format: inputFormat,
            }),
          },
        );
        if (!res.ok) {
          throw new SttError("network", `Session mint failed (${res.status}).`);
        }
        const payload = (await res.json()) as {
          session?: { client_secret?: string; websocket_url?: string };
        };
        secret = payload?.session?.client_secret ?? "";
        wsUrlFromMint = payload?.session?.websocket_url;
        if (!secret)
          throw new SttError("network", "No session secret returned.");
      } catch (err) {
        if (err instanceof SttError) throw err;
        throw new SttError("network", "Could not start a streaming session.");
      }

      // 2. Open the socket and wire events.
      await new Promise<void>((resolve, reject) => {
        try {
          // Default: derive the WS from baseUrl (proxy origin) — the gateway's
          // own websocket_url host is blocked by the host page's CSP. Opt into
          // the direct gateway URL only when directWs is set.
          const wsUrl = directWs
            ? resolveWsUrl(baseUrl, secret, wsUrlFromMint)
            : realtimeWsUrl(baseUrl, secret);
          ws = new WS(wsUrl);
        } catch {
          reject(new SttError("network", "Could not open the audio stream."));
          return;
        }
        ws!.onopen = () => {
          open = true;
          // Container mode needs a WAV header so the concatenated stream stays
          // decodable. On the pcm16 path a header would be transcribed as noise.
          if (inputFormat === "auto") {
            send({
              type: "input_audio.append",
              audio: toBase64(buildWavHeader(sampleRate)),
            });
          }
          for (const text of queue.splice(0)) ws!.send(text);
          resolve();
        };
        ws!.onerror = () => {
          if (!open)
            reject(new SttError("network", "Audio stream failed to open."));
          else cb.onError(new SttError("network", "Audio stream error."));
        };
        ws!.onclose = () => {
          open = false;
          // Never leave finish() hanging on a socket that went away.
          awaitTail?.(full());
        };
        ws!.onmessage = (evt: MessageEvent) => {
          let msg: any;
          try {
            msg = JSON.parse(typeof evt.data === "string" ? evt.data : "");
          } catch {
            return;
          }
          if (msg?.type === "transcript.delta") {
            // Deltas are incremental fragments — accumulate, don't replace.
            partial += String(msg.delta ?? "");
            cb.onPartial?.(full());
          } else if (msg?.type === "transcript.completed") {
            const text = String(msg.transcript ?? "").trim();
            if (text) turns.push(text);
            partial = "";
            pendingBytes = 0;
            cb.onTurn?.(full());
            awaitTail?.(full());
          } else if (msg?.type === "error") {
            cb.onError(
              new SttError("network", String(msg.message ?? "Stream error")),
            );
          } else if (msg?.type === "session.closed") {
            awaitTail?.(full());
          }
        };
      });
    },

    sendChunk(pcm) {
      pendingBytes += pcm.byteLength;
      send({ type: "input_audio.append", audio: toBase64(pcm) });
    },

    finish(timeoutMs = DEFAULT_FINISH_TIMEOUT_MS) {
      // Nothing un-finalised: every turn already came back.
      if (!ws || (pendingBytes < MIN_TAIL_BYTES && !partial)) {
        return Promise.resolve(full());
      }

      return new Promise<string>((resolve) => {
        let settled = false;
        let graceTimer: ReturnType<typeof setTimeout> | null = null;
        const settle = () => {
          if (settled) return;
          settled = true;
          if (graceTimer) clearTimeout(graceTimer);
          clearTimeout(hardTimer);
          awaitTail = null;
          resolve(full());
        };
        const hardTimer = setTimeout(settle, timeoutMs);
        // A turn landed — give queued sibling turns a moment before settling.
        awaitTail = () => {
          if (settled || graceTimer) return;
          graceTimer = setTimeout(settle, TURN_GRACE_MS);
        };

        if (inputFormat === "auto") send({ type: "input_audio.commit" });
        // commit() drops the in-VAD tail on the pcm16 path; trailing silence is
        // what makes the server close and transcribe that last turn.
        else sendSilence();
      });
    },

    commit() {
      send({ type: "input_audio.commit" });
    },

    close() {
      open = false;
      awaitTail?.(full());
      awaitTail = null;
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
      ws = null;
    },
  };
}
