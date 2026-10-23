/** Lifecycle states for the text-to-speech controller. */
export type TtsStatus =
  | "idle" // nothing playing; ready
  | "loading" // synthesizing audio from the gateway
  | "playing" // audio is playing
  | "paused" // playback held; resume continues from the same position
  | "error"; // last action failed

/** Categorised failures so the UI can show the right recovery copy. */
export type TtsErrorKind =
  | "network" // request failed (offline, 5xx, 401/403)
  | "rate-limited" // gateway returned 429
  | "unsupported" // fetch / Audio missing in this environment
  | "aborted" // playback cancelled before audio produced
  | "empty-text" // nothing to read aloud
  | "unknown";

export class TtsError extends Error {
  kind: TtsErrorKind;
  constructor(kind: TtsErrorKind, message?: string) {
    super(message ?? kind);
    this.name = "TtsError";
    this.kind = kind;
  }
}

/** Result of one synthesis call. */
export interface SynthesisResult {
  blob: Blob;
  /** Object URL for the blob; retained for in-session replay and revoked by useTextToSpeech on unmount. */
  url: string;
  /** Estimated duration from the gateway header, or null when unknown. */
  durationSeconds: number | null;
  mimeType: string;
}

/** Turns text into a playable audio result. */
export type Synthesizer = (
  text: string,
  options?: { signal?: AbortSignal },
) => Promise<SynthesisResult>;
