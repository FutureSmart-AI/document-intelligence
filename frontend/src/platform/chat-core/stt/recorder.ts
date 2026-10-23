import { RecorderHandle, SttError } from "./types";

/** Candidate container/codec pairs, most-preferred first. */
const PREFERRED_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

export function isRecordingSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined"
  );
}

function pickMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  if (typeof MediaRecorder.isTypeSupported !== "function") return "";
  for (const type of PREFERRED_MIME_TYPES) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

/** Map a getUserMedia rejection onto one of our error kinds. */
function classifyGetUserMediaError(err: unknown): SttError {
  const name = (err as { name?: string })?.name ?? "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return new SttError("permission-denied", "Microphone access was denied.");
    case "NotFoundError":
    case "OverconstrainedError":
      return new SttError("no-device", "No microphone was found.");
    case "NotReadableError":
      return new SttError("no-device", "The microphone is already in use.");
    default:
      return new SttError("unknown", (err as Error)?.message);
  }
}

/**
 * Thin wrapper around MediaRecorder that exposes a promise-based start/stop API
 * and guarantees the mic stream is released on stop or cancel.
 */
export function createRecorder(): RecorderHandle {
  if (!isRecordingSupported()) {
    throw new SttError(
      "unsupported",
      "Audio recording is not supported in this browser.",
    );
  }

  const mimeType = pickMimeType();
  let stream: MediaStream | null = null;
  let recorder: MediaRecorder | null = null;
  let chunks: Blob[] = [];

  const releaseStream = () => {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
  };

  return {
    get mimeType() {
      return recorder?.mimeType || mimeType;
    },

    async start() {
      chunks = [];
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        throw classifyGetUserMediaError(err);
      }
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunks.push(event.data);
      };
      recorder.start();
    },

    stop() {
      return new Promise<Blob>((resolve, reject) => {
        if (!recorder) {
          reject(new SttError("aborted", "Recorder was not started."));
          return;
        }
        recorder.onstop = () => {
          const blob = new Blob(chunks, {
            type: recorder?.mimeType || mimeType || "audio/webm",
          });
          releaseStream();
          if (blob.size === 0) {
            reject(new SttError("aborted", "No audio was captured."));
            return;
          }
          resolve(blob);
        };
        try {
          recorder.stop();
        } catch (err) {
          releaseStream();
          reject(new SttError("unknown", (err as Error)?.message));
        }
      });
    },

    cancel() {
      try {
        if (recorder && recorder.state !== "inactive") {
          recorder.onstop = null;
          recorder.stop();
        }
      } catch {
        // already stopped — ignore
      } finally {
        releaseStream();
        chunks = [];
      }
    },
  };
}
