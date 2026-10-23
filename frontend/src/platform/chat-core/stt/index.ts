export {
  useSpeechToText,
  type UseSpeechToTextOptions,
  type UseSpeechToTextResult,
} from "./useSpeechToText";
export {
  useStreamingSpeechToText,
  type UseStreamingSpeechToTextOptions,
  type UseStreamingSpeechToTextResult,
} from "./useStreamingSpeechToText";
export { DEFAULT_PREFERRED_LANGUAGES, normalizeLanguage } from "./config";
export { createTranscriber, type TranscriberConfig } from "./transcriber";
export {
  createRealtimeSession,
  realtimeWsUrl,
  resolveWsUrl,
  joinTranscript,
  REALTIME_SAMPLE_RATE,
  type RealtimeConfig,
  type RealtimeSession,
  type RealtimeCallbacks,
} from "./realtimeTranscriber";
export { createRecorder, isRecordingSupported } from "./recorder";
export {
  createPcmRecorder,
  isStreamingSupported,
  isMicAllowedByPolicy,
  floatToPcm16,
  buildWavHeader,
  createResampler,
  type PcmRecorderHandle,
  type PcmRecorderOptions,
} from "./pcmRecorder";
export {
  SttError,
  type SttErrorKind,
  type SttStatus,
  type Transcriber,
  type RecorderHandle,
} from "./types";

/** Format elapsed milliseconds as "m:ss" for the recording timer. */
export function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/**
 * Human-readable, recoverable message for each error kind — shared so the
 * widget and dashboard show identical copy.
 */
export function sttErrorMessage(kind: string | null): string {
  switch (kind) {
    case "permission-denied":
      return "Microphone access is blocked — by your browser settings or by this site. Check your browser's site settings, or attach an audio file instead.";
    case "no-device":
      return "No microphone was found. Check that one is connected and not in use by another app.";
    case "unsupported":
      return "Voice input isn't supported in this browser. Try a recent version of Chrome, Edge, or Safari.";
    case "no-speech":
      return "We couldn't hear anything. Try recording again.";
    case "too-short":
      return "That recording was too short. Hold the mic button a little longer and try again.";
    case "network":
      return "Couldn't reach the transcription service. Check your connection and try again.";
    default:
      return "Something went wrong while recording. Please try again.";
  }
}
