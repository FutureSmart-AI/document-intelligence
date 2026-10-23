// packages/chat-core/src/tts/useTextToSpeech.ts
import { useCallback, useEffect, useRef, useState } from "react";

import { SynthesisResult, Synthesizer, TtsError, TtsStatus } from "./types";

/**
 * Only one message plays at a time across the page. Each hook registers its
 * stopper here on play; starting another stops the previous one.
 */
let activeStop: (() => void) | null = null;

export interface UseTextToSpeechOptions {
  synthesize: Synthesizer;
  onError?: (error: TtsError) => void;
  /** Test seam: factory for the audio element. */
  createAudioImpl?: (url: string) => HTMLAudioElement;
}

export interface UseTextToSpeechResult {
  status: TtsStatus;
  error: TtsError | null;
  play: (text: string) => Promise<void>;
  /** Hold playback in place; resume() continues from the same position. */
  pause: () => void;
  /** Continue a paused clip from where it stopped. */
  resume: () => void;
  /** Tear down playback and return to idle (used on unmount / message switch). */
  stop: () => void;
}

function toTtsError(err: unknown): TtsError {
  if (err instanceof TtsError) return err;
  return new TtsError("unknown", (err as Error)?.message);
}

export function useTextToSpeech(
  options: UseTextToSpeechOptions,
): UseTextToSpeechResult {
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const [status, setStatus] = useState<TtsStatus>("idle");
  const [error, setError] = useState<TtsError | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Single-entry cache so replaying the same text skips the network.
  const cacheRef = useRef<{ text: string; result: SynthesisResult } | null>(
    null,
  );

  const teardownAudio = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.onended = null;
      audioRef.current = null;
    }
  }, []);

  // stop() is referenced by the module-level registry; keep it in a ref so the
  // registry always calls the latest closure.
  const stopRef = useRef<() => void>(() => {});

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    teardownAudio();
    setStatus("idle");
    if (activeStop === stopRef.current) activeStop = null;
  }, [teardownAudio]);
  stopRef.current = stop;

  // Pause keeps the audio element (and its currentTime) so resume continues
  // from the same spot instead of re-synthesizing from the beginning.
  const pause = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.pause();
    setStatus("paused");
  }, []);

  const resume = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    // Re-assert one-at-a-time: stop whatever else may have started while paused.
    if (activeStop && activeStop !== stopRef.current) activeStop();
    activeStop = stopRef.current;
    setStatus("playing");
    void audio.play().catch(() => {
      // Autoplay rejections leave us in "playing"; the user can press again.
    });
  }, []);

  const play = useCallback(
    async (text: string) => {
      // Stop any other message first, then clean up our own previous playback.
      if (activeStop && activeStop !== stopRef.current) activeStop();
      teardownAudio();
      setError(null);

      const trimmed = (text ?? "").trim();
      if (!trimmed) {
        const err = new TtsError("empty-text", "Nothing to read aloud.");
        setStatus("error");
        setError(err);
        optionsRef.current.onError?.(err);
        return;
      }

      let result: SynthesisResult;
      try {
        if (cacheRef.current?.text === trimmed) {
          result = cacheRef.current.result;
        } else {
          setStatus("loading");
          const controller = new AbortController();
          abortRef.current = controller;
          result = await optionsRef.current.synthesize(trimmed, {
            signal: controller.signal,
          });
          // A stop() or newer play() during the await supersedes this call — bail without
          // starting audio (stop already set status; a newer play owns the state now).
          if (abortRef.current !== controller) return;
          abortRef.current = null;
          cacheRef.current = { text: trimmed, result };
        }
      } catch (err) {
        const ttsErr = toTtsError(err);
        if (ttsErr.kind === "aborted") {
          setStatus("idle");
          return;
        }
        setStatus("error");
        setError(ttsErr);
        optionsRef.current.onError?.(ttsErr);
        return;
      }

      const audio = (
        optionsRef.current.createAudioImpl ?? ((u: string) => new Audio(u))
      )(result.url);
      audioRef.current = audio;
      audio.onended = () => {
        teardownAudio();
        setStatus("idle");
        if (activeStop === stopRef.current) activeStop = null;
      };

      activeStop = stopRef.current;
      setStatus("playing");
      try {
        await audio.play();
      } catch {
        // Autoplay rejections leave us in "playing"; the user can press stop.
      }
    },
    [teardownAudio],
  );

  useEffect(
    () => () => {
      abortRef.current?.abort();
      teardownAudio();
      if (cacheRef.current) {
        URL.revokeObjectURL?.(cacheRef.current.result.url);
        cacheRef.current = null;
      }
      if (activeStop === stopRef.current) activeStop = null;
    },
    [teardownAudio],
  );

  return { status, error, play, pause, resume, stop };
}
