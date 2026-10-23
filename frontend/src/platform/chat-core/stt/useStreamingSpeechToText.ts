import { useCallback, useEffect, useRef, useState } from "react";

import {
  buildWavHeader,
  concatChunks,
  createPcmRecorder,
  isMicAllowedByPolicy,
  isStreamingSupported,
  PcmRecorderHandle,
} from "./pcmRecorder";
import {
  createRealtimeSession,
  REALTIME_SAMPLE_RATE,
  type RealtimeConfig,
  type RealtimeSession,
} from "./realtimeTranscriber";
import { createRecorder, isRecordingSupported } from "./recorder";
import {
  RecorderHandle,
  SttError,
  SttErrorKind,
  SttStatus,
  Transcriber,
} from "./types";

const DEFAULT_MAX_DURATION_MS = 120_000;
const DEFAULT_MIN_DURATION_MS = 1_000;
const TIMER_TICK_MS = 200;
/** How often to re-transcribe the growing clip for the live transcript. */
const DEFAULT_LIVE_INTERVAL_MS = 500;
/** Max wait for the gateway to transcribe the flushed tail turn on stop. */
const REALTIME_FINAL_TIMEOUT_MS = 4000;

export interface UseStreamingSpeechToTextOptions {
  /**
   * Turns recorded audio into text. With OpenAI (no streaming partials) we get
   * the live effect by re-transcribing the growing clip on an interval.
   */
  transcribe: Transcriber;
  /**
   * Enables the true realtime path: mint a WS session, stream 24 kHz PCM live,
   * and accumulate `transcript.delta` / per-turn `transcript.completed` events
   * into a transcript that grows while the user is still speaking. When the
   * session can't start (e.g. the STT proxy doesn't forward POST/WS) — or dies
   * mid-utterance — the hook transparently falls back to re-transcribing the
   * growing clip via {@link transcribe}. Omit to use only the batch path.
   */
  realtime?: RealtimeConfig;
  maxDurationMs?: number;
  minDurationMs?: number;
  /** Re-transcribe cadence for the live transcript (ms). Defaults to 2000. */
  liveIntervalMs?: number;
  onTranscript?: (text: string) => void;
  onError?: (error: SttError) => void;
  // Test injection points.
  createPcmRecorderImpl?: (opts: {
    onChunk: (pcm: Uint8Array) => void;
    sampleRate?: number;
    targetSampleRate?: number;
  }) => PcmRecorderHandle;
  createRecorderImpl?: () => RecorderHandle;
  createRealtimeSessionImpl?: (config: RealtimeConfig) => RealtimeSession;
}

export interface UseStreamingSpeechToTextResult {
  status: SttStatus;
  elapsedMs: number;
  error: SttError | null;
  errorKind: SttErrorKind | null;
  isSupported: boolean;
  isRecording: boolean;
  isBusy: boolean;
  /** Live transcript that grows while the user speaks. */
  interimTranscript: string;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  cancel: () => void;
  reset: () => void;
}

function toSttError(err: unknown): SttError {
  if (err instanceof SttError) return err;
  return new SttError("unknown", (err as Error)?.message);
}

/** Assemble accumulated PCM16 chunks into a complete WAV blob. */
function pcmToWav(chunks: Uint8Array[], sampleRate: number): Blob {
  const pcm = concatChunks(chunks);
  const header = buildWavHeader(sampleRate, 1, 16, pcm.byteLength);
  // Cast: a Uint8Array is a valid BlobPart at runtime; the generic
  // Uint8Array<ArrayBufferLike> type confuses some lib.dom versions.
  return new Blob([header, pcm] as BlobPart[], { type: "audio/wav" });
}

export function useStreamingSpeechToText(
  options: UseStreamingSpeechToTextOptions,
): UseStreamingSpeechToTextResult {
  const {
    maxDurationMs = DEFAULT_MAX_DURATION_MS,
    minDurationMs = DEFAULT_MIN_DURATION_MS,
    liveIntervalMs = DEFAULT_LIVE_INTERVAL_MS,
  } = options;

  const optionsRef = useRef(options);
  optionsRef.current = options;

  const [status, setStatus] = useState<SttStatus>("idle");
  const [elapsedMs, setElapsedMs] = useState(0);
  const [error, setError] = useState<SttError | null>(null);
  const [interimTranscript, setInterimTranscript] = useState("");

  // "realtime" = live WS stream; "pcm" = AudioWorklet + re-transcribe;
  // "batch" = MediaRecorder fallback (no live).
  const modeRef = useRef<"realtime" | "pcm" | "batch" | null>(null);
  const pcmRef = useRef<PcmRecorderHandle | null>(null);
  const batchRef = useRef<RecorderHandle | null>(null);
  const realtimeRef = useRef<RealtimeSession | null>(null);
  const chunksRef = useRef<Uint8Array[]>([]);
  const sampleRateRef = useRef(16000);
  const lastBytesRef = useRef(0);
  const inFlightRef = useRef(false);
  // Latest interim text, mirrored so stop() can read the freshest value.
  const interimRef = useRef("");
  const startedAtRef = useRef(0);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const maxTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const liveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Gate on the host page's Permissions-Policy first: if the embedding page
  // sets `microphone=()`, getUserMedia throws NotAllowedError with no prompt,
  // so the mic button must not render at all (it could never work there).
  const isSupported =
    isMicAllowedByPolicy() &&
    (isStreamingSupported() || isRecordingSupported());

  // Keep the ref in lockstep with interim state so the commit-timeout can read
  // the freshest value synchronously.
  const setInterim = useCallback((text: string) => {
    interimRef.current = text;
    setInterimTranscript(text);
  }, []);

  const clearTimers = useCallback(() => {
    if (tickRef.current) clearInterval(tickRef.current);
    if (maxTimerRef.current) clearTimeout(maxTimerRef.current);
    if (liveTimerRef.current) clearInterval(liveTimerRef.current);
    tickRef.current = null;
    maxTimerRef.current = null;
    liveTimerRef.current = null;
  }, []);

  const releaseAll = useCallback(() => {
    try {
      pcmRef.current?.cancel();
      batchRef.current?.cancel();
      realtimeRef.current?.close();
    } catch {
      /* ignore */
    }
    pcmRef.current = null;
    batchRef.current = null;
    realtimeRef.current = null;
    chunksRef.current = [];
    lastBytesRef.current = 0;
    inFlightRef.current = false;
    modeRef.current = null;
  }, []);

  const fail = useCallback(
    (err: unknown) => {
      clearTimers();
      releaseAll();
      const sttErr = toSttError(err);
      setStatus("error");
      setInterim("");
      setError(sttErr);
      optionsRef.current.onError?.(sttErr);
    },
    [clearTimers, releaseAll],
  );

  const finalize = useCallback(
    (text: string) => {
      clearTimers();
      releaseAll();
      setStatus("idle");
      setElapsedMs(0);
      setInterim("");
      optionsRef.current.onTranscript?.(text);
    },
    [clearTimers, releaseAll],
  );

  // Re-transcribe the growing clip → live interim text. Errors here are
  // swallowed (transient); the final pass on stop surfaces real failures.
  const reTranscribe = useCallback(async () => {
    if (inFlightRef.current || modeRef.current !== "pcm") return;
    const chunks = chunksRef.current;
    const bytes = chunks.reduce((n, c) => n + c.byteLength, 0);
    if (bytes === 0 || bytes === lastBytesRef.current) return;
    lastBytesRef.current = bytes;
    inFlightRef.current = true;
    try {
      const wav = pcmToWav(chunks, sampleRateRef.current);
      const text = await optionsRef.current.transcribe(wav);
      // Ignore if we stopped/cancelled while the request was in flight.
      if (modeRef.current === "pcm" && text) setInterim(text);
    } catch {
      /* transient — keep going */
    } finally {
      inFlightRef.current = false;
    }
  }, []);

  /** (Re)arm the pseudo-live re-transcribe loop. */
  const startLiveRetranscribe = useCallback(() => {
    if (liveTimerRef.current) clearInterval(liveTimerRef.current);
    liveTimerRef.current = setInterval(() => {
      void reTranscribe();
    }, liveIntervalMs);
  }, [reTranscribe, liveIntervalMs]);

  // The socket died mid-utterance (the gateway does no mid-session failover).
  // The mic is still open and every chunk is also buffered locally, so switch to
  // re-transcribing the growing clip rather than discarding what was said. The
  // existing interim text stays on screen until the first re-transcribe lands.
  const degradeToPseudoLive = useCallback(() => {
    if (modeRef.current !== "realtime") return;
    try {
      realtimeRef.current?.close();
    } catch {
      /* ignore */
    }
    realtimeRef.current = null;
    modeRef.current = "pcm";
    startLiveRetranscribe();
  }, [startLiveRetranscribe]);

  const beginTimers = useCallback(() => {
    startedAtRef.current = Date.now();
    setStatus("recording");
    tickRef.current = setInterval(() => {
      setElapsedMs(Date.now() - startedAtRef.current);
    }, TIMER_TICK_MS);
    maxTimerRef.current = setTimeout(() => {
      void stopRef.current();
    }, maxDurationMs);
  }, [maxDurationMs]);

  // MediaRecorder path: transcribe once on stop. No AudioWorklet, so it works
  // where the live PCM path can't initialize (e.g. a host CSP that blocks the
  // blob: worklet module). Throws a classified SttError on failure.
  const startBatch = useCallback(async () => {
    const rec = (optionsRef.current.createRecorderImpl ?? createRecorder)();
    batchRef.current = rec;
    await rec.start();
    modeRef.current = "batch";
    beginTimers();
  }, [beginTimers]);

  const start = useCallback(async () => {
    if (status === "recording" || status === "transcribing") return;
    setError(null);
    setElapsedMs(0);
    setInterim("");
    setStatus("requesting");
    chunksRef.current = [];
    lastBytesRef.current = 0;

    const opts = optionsRef.current;

    if (opts.createPcmRecorderImpl || isStreamingSupported()) {
      // Live path: capture PCM. When realtime is configured we stream it to the
      // gateway over a WebSocket; the same chunks also accumulate so the
      // pseudo-live re-transcribe path can take over if the session can't start.
      const pcm = (opts.createPcmRecorderImpl ?? createPcmRecorder)({
        // The realtime path only accepts 24 kHz PCM16; native-rate samples come
        // back as sped-up gibberish.
        targetSampleRate: opts.realtime ? REALTIME_SAMPLE_RATE : undefined,
        onChunk: (bytes) => {
          chunksRef.current.push(bytes);
          if (modeRef.current === "realtime")
            realtimeRef.current?.sendChunk(bytes);
        },
      });
      pcmRef.current = pcm;
      try {
        await pcm.start();
      } catch (err) {
        // Permission/device failures would recur on the batch path too, so
        // surface them. Anything else (e.g. AudioWorklet setup blocked by a
        // host CSP) is specific to the live path — fall back to MediaRecorder
        // rather than dead-ending on the generic "something went wrong".
        const kind = err instanceof SttError ? err.kind : "unknown";
        if (kind === "permission-denied" || kind === "no-device") {
          fail(err);
          return;
        }
        releaseAll();
        try {
          await startBatch();
        } catch (batchErr) {
          fail(batchErr);
        }
        return;
      }
      sampleRateRef.current = pcm.sampleRate;

      // Prefer the true realtime WS stream when configured. Set the mode before
      // starting so chunks captured while the socket opens are queued, not lost.
      if (opts.realtime) {
        const session = (
          opts.createRealtimeSessionImpl ?? createRealtimeSession
        )(opts.realtime);
        realtimeRef.current = session;
        modeRef.current = "realtime";
        try {
          await session.start(pcm.sampleRate, {
            // Both events carry the FULL transcript so far (finalised turns +
            // in-flight deltas), so the interim only ever grows.
            onPartial: (t) => {
              if (modeRef.current === "realtime") setInterim(t);
            },
            onTurn: (t) => {
              if (modeRef.current === "realtime") setInterim(t);
            },
            onError: () => degradeToPseudoLive(),
          });
          beginTimers();
          return;
        } catch {
          // Session couldn't start (e.g. the STT proxy doesn't forward POST/WS).
          // The mic is already open — reuse it for the pseudo-live path below
          // instead of re-prompting the user.
          try {
            realtimeRef.current?.close();
          } catch {
            /* ignore */
          }
          realtimeRef.current = null;
          modeRef.current = null;
        }
      }

      // Pseudo-live: re-transcribe the growing clip on a timer.
      modeRef.current = "pcm";
      beginTimers();
      startLiveRetranscribe();
      return;
    }

    // No AudioWorklet support at all: go straight to the MediaRecorder path.
    try {
      await startBatch();
    } catch (err) {
      fail(err);
    }
  }, [
    status,
    fail,
    beginTimers,
    startLiveRetranscribe,
    degradeToPseudoLive,
    releaseAll,
    startBatch,
    setInterim,
  ]);

  const stop = useCallback(async () => {
    if (status !== "recording") return;
    clearTimers();

    const elapsed = Date.now() - startedAtRef.current;
    if (minDurationMs > 0 && elapsed < minDurationMs) {
      releaseAll();
      setElapsedMs(0);
      fail(
        new SttError(
          "too-short",
          "Recording too short. Hold the mic a little longer.",
        ),
      );
      return;
    }

    setStatus("transcribing");

    // Realtime: flush the trailing PCM into the socket, let the gateway close
    // and transcribe the last turn, then settle on the accumulated transcript.
    if (modeRef.current === "realtime") {
      const session = realtimeRef.current;
      pcmRef.current?.stop(); // flushes the last samples through sendChunk
      let finalText = "";
      try {
        finalText = (await session?.finish(REALTIME_FINAL_TIMEOUT_MS)) ?? "";
      } catch {
        finalText = "";
      }
      if (!finalText.trim()) finalText = interimRef.current;
      // Last resort: the socket gave us nothing at all (session dropped early),
      // but the audio is buffered locally — transcribe it in one batch call so
      // the user doesn't lose the utterance.
      if (!finalText.trim() && chunksRef.current.length) {
        try {
          const wav = pcmToWav(chunksRef.current, sampleRateRef.current);
          finalText = await optionsRef.current.transcribe(wav);
        } catch (err) {
          fail(err);
          return;
        }
      }
      finalize(finalText);
      return;
    }

    try {
      let wav: Blob;
      if (modeRef.current === "pcm") {
        pcmRef.current?.stop(); // flushes the last samples into chunksRef
        wav = pcmToWav(chunksRef.current, sampleRateRef.current);
      } else {
        const rec = batchRef.current;
        batchRef.current = null;
        wav = await rec!.stop();
      }
      const text = await optionsRef.current.transcribe(wav);
      finalize(text);
    } catch (err) {
      fail(err);
    }
  }, [status, clearTimers, minDurationMs, fail, finalize, releaseAll]);

  const stopRef = useRef(stop);
  stopRef.current = stop;

  const cancel = useCallback(() => {
    clearTimers();
    releaseAll();
    setStatus("idle");
    setElapsedMs(0);
    setInterim("");
    setError(null);
  }, [clearTimers, releaseAll]);

  const reset = useCallback(() => {
    setStatus("idle");
    setError(null);
    setElapsedMs(0);
    setInterim("");
  }, []);

  useEffect(() => {
    return () => {
      clearTimers();
      releaseAll();
    };
  }, [clearTimers, releaseAll]);

  return {
    status,
    elapsedMs,
    error,
    errorKind: error?.kind ?? null,
    isSupported,
    isRecording: status === "recording",
    isBusy: status === "requesting" || status === "transcribing",
    interimTranscript,
    start,
    stop,
    cancel,
    reset,
  };
}
