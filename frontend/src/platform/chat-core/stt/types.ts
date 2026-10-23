/** Lifecycle states for the speech-to-text controller. */
export type SttStatus =
  | "idle" // nothing happening; ready to record
  | "requesting" // asked the browser for mic permission, awaiting the prompt
  | "recording" // actively capturing audio
  | "transcribing" // audio captured, waiting on the STT backend
  | "error"; // last action failed (see `errorKind`)

/** Categorised failures so the UI can show the right recovery flow. */
export type SttErrorKind =
  | "permission-denied" // user blocked the mic, or it is blocked by policy
  | "no-device" // no microphone hardware available
  | "unsupported" // MediaRecorder / getUserMedia missing in this browser
  | "no-speech" // recording succeeded but transcript came back empty
  | "too-short" // recording stopped almost immediately (likely an accidental tap)
  | "network" // transcription request failed (offline, 5xx, timeout)
  | "aborted" // recording was cancelled before producing audio
  | "unknown";

export class SttError extends Error {
  kind: SttErrorKind;
  constructor(kind: SttErrorKind, message?: string) {
    super(message ?? kind);
    this.name = "SttError";
    this.kind = kind;
  }
}

/** A function that turns recorded audio into text. */
export type Transcriber = (
  audio: Blob,
  options?: { signal?: AbortSignal },
) => Promise<string>;

export interface RecorderHandle {
  /** Request mic permission and begin capturing. Rejects with {@link SttError}. */
  start: () => Promise<void>;
  /** Stop capturing and resolve with the recorded audio. */
  stop: () => Promise<Blob>;
  /** Abort capturing and release the mic without producing a blob. */
  cancel: () => void;
  /** MIME type the recorder settled on (e.g. "audio/webm"). */
  readonly mimeType: string;
}
