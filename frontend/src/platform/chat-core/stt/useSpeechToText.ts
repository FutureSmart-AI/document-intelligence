import { useCallback, useEffect, useRef, useState } from "react";

import { isMicAllowedByPolicy } from "./pcmRecorder";
import { createRecorder, isRecordingSupported } from "./recorder";
import {
  RecorderHandle,
  SttError,
  SttErrorKind,
  SttStatus,
  Transcriber,
} from "./types";

/** Hard cap on recording length — matches the 2-minute auto-stop in the design. */
const DEFAULT_MAX_DURATION_MS = 120_000;
/** Below this, a recording is treated as an accidental tap, not speech. */
const DEFAULT_MIN_DURATION_MS = 1_000;
const TIMER_TICK_MS = 200;

export interface UseSpeechToTextOptions {
  /** Turns recorded audio into text (see {@link createTranscriber}). */
  transcribe: Transcriber;
  /** Auto-stop after this many ms. Defaults to 120000 (2 minutes). */
  maxDurationMs?: number;
  /**
   * Reject recordings shorter than this (ms) as "too-short" instead of
   * transcribing them. Defaults to 1000 (1 second). Set to 0 to disable.
   */
  minDurationMs?: number;
  /** Called with the recognised transcript once transcription completes. */
  onTranscript?: (text: string) => void;
  /** Called whenever an error occurs. */
  onError?: (error: SttError) => void;
  /** Injectable recorder factory (tests). */
  createRecorderImpl?: () => RecorderHandle;
}

export interface UseSpeechToTextResult {
  status: SttStatus;
  /** Milliseconds elapsed in the current recording. */
  elapsedMs: number;
  error: SttError | null;
  errorKind: SttErrorKind | null;
  isSupported: boolean;
  isRecording: boolean;
  /** True while requesting permission or transcribing. */
  isBusy: boolean;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  cancel: () => void;
  /** Clear an error and return to idle. */
  reset: () => void;
}

function toSttError(err: unknown): SttError {
  if (err instanceof SttError) return err;
  return new SttError("unknown", (err as Error)?.message);
}

export function useSpeechToText(
  options: UseSpeechToTextOptions,
): UseSpeechToTextResult {
  const {
    maxDurationMs = DEFAULT_MAX_DURATION_MS,
    minDurationMs = DEFAULT_MIN_DURATION_MS,
  } = options;

  // Keep callbacks/transcriber in refs so start/stop stay referentially stable.
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const [status, setStatus] = useState<SttStatus>("idle");
  const [elapsedMs, setElapsedMs] = useState(0);
  const [error, setError] = useState<SttError | null>(null);

  const recorderRef = useRef<RecorderHandle | null>(null);
  const startedAtRef = useRef(0);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const maxTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isSupported = isMicAllowedByPolicy() && isRecordingSupported();

  const clearTimers = useCallback(() => {
    if (tickRef.current) clearInterval(tickRef.current);
    if (maxTimerRef.current) clearTimeout(maxTimerRef.current);
    tickRef.current = null;
    maxTimerRef.current = null;
  }, []);

  const fail = useCallback((err: unknown) => {
    const sttErr = toSttError(err);
    setStatus("error");
    setError(sttErr);
    optionsRef.current.onError?.(sttErr);
  }, []);

  const stop = useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder || status !== "recording") return;
    clearTimers();

    // Guard against accidental taps: a sub-second recording is almost never
    // speech, so drop it instead of paying for a transcription round-trip.
    const elapsed = Date.now() - startedAtRef.current;
    if (minDurationMs > 0 && elapsed < minDurationMs) {
      recorder.cancel();
      recorderRef.current = null;
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
    try {
      const audio = await recorder.stop();
      recorderRef.current = null;
      const text = await optionsRef.current.transcribe(audio);
      setStatus("idle");
      setElapsedMs(0);
      optionsRef.current.onTranscript?.(text);
    } catch (err) {
      recorderRef.current = null;
      fail(err);
    }
  }, [status, clearTimers, fail, minDurationMs]);

  const start = useCallback(async () => {
    if (status === "recording" || status === "transcribing") return;
    setError(null);
    setElapsedMs(0);
    setStatus("requesting");
    try {
      const recorder = (
        optionsRef.current.createRecorderImpl ?? createRecorder
      )();
      recorderRef.current = recorder;
      await recorder.start();
      startedAtRef.current = Date.now();
      setStatus("recording");

      tickRef.current = setInterval(() => {
        setElapsedMs(Date.now() - startedAtRef.current);
      }, TIMER_TICK_MS);

      maxTimerRef.current = setTimeout(() => {
        // Auto-stop via the latest stop() in the ref.
        void stopRef.current();
      }, maxDurationMs);
    } catch (err) {
      recorderRef.current = null;
      fail(err);
    }
  }, [status, maxDurationMs, fail]);

  // stop() is recreated when `status` changes; the auto-stop timer needs the
  // current version, so route it through a ref.
  const stopRef = useRef(stop);
  stopRef.current = stop;

  const cancel = useCallback(() => {
    clearTimers();
    recorderRef.current?.cancel();
    recorderRef.current = null;
    setStatus("idle");
    setElapsedMs(0);
    setError(null);
  }, [clearTimers]);

  const reset = useCallback(() => {
    setStatus("idle");
    setError(null);
    setElapsedMs(0);
  }, []);

  // Release the mic if the component unmounts mid-recording.
  useEffect(() => {
    return () => {
      clearTimers();
      recorderRef.current?.cancel();
      recorderRef.current = null;
    };
  }, [clearTimers]);

  return {
    status,
    elapsedMs,
    error,
    errorKind: error?.kind ?? null,
    isSupported,
    isRecording: status === "recording",
    isBusy: status === "requesting" || status === "transcribing",
    start,
    stop,
    cancel,
    reset,
  };
}
