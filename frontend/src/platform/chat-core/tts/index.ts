export { createSynthesizer, type SynthesizerConfig } from "./synthesizer";
export {
  useTextToSpeech,
  type UseTextToSpeechOptions,
  type UseTextToSpeechResult,
} from "./useTextToSpeech";
export { toPlainText } from "./toPlainText";
export {
  TtsError,
  type TtsErrorKind,
  type TtsStatus,
  type Synthesizer,
  type SynthesisResult,
} from "./types";
export {
  DEFAULT_VOICE,
  DEFAULT_MODEL,
  DEFAULT_FORMAT,
  DEFAULT_SPEED,
} from "./config";

/** Human-readable, recoverable copy for each TTS error kind. */
export function ttsErrorMessage(kind: string | null): string {
  switch (kind) {
    case "rate-limited":
      return "Too many read-aloud requests right now. Try again in a moment.";
    case "network":
      return "Couldn't reach the speech service. Check your connection and try again.";
    case "unsupported":
      return "Read-aloud isn't supported in this browser. Try a recent Chrome, Edge, or Safari.";
    case "empty-text":
      return "There's nothing to read aloud here.";
    default:
      return "Something went wrong while reading aloud. Please try again.";
  }
}
