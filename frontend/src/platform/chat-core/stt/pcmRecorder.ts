import { SttError, SttErrorKind } from "./types";

/**
 * Streaming microphone capture for the realtime STT path.
 *
 * `MediaRecorder` only yields compressed containers (webm/opus) whose chunks
 * don't concatenate into a valid file, so it can't feed an incremental stream.
 * Instead we tap raw PCM via the Web Audio API + an AudioWorklet, convert to
 * 16-bit little-endian samples, and emit them as a WAV stream (header first,
 * then PCM frames) — valid for the gateway's magic-byte check and streamable.
 */

/** Convert Float32 audio samples ([-1, 1]) to little-endian PCM16 bytes. */
export function floatToPcm16(samples: Float32Array): Uint8Array {
  const out = new Uint8Array(samples.length * 2);
  const view = new DataView(out.buffer);
  for (let i = 0; i < samples.length; i++) {
    let s = samples[i];
    if (s > 1) s = 1;
    else if (s < -1) s = -1;
    // Negative range is one larger than positive for symmetric 16-bit PCM.
    const val = s < 0 ? s * 0x8000 : s * 0x7fff;
    view.setInt16(i * 2, val | 0, true);
  }
  return out;
}

/**
 * Build a 44-byte canonical WAV header. `dataSize` defaults to a placeholder
 * (0xFFFFFFFF) for an open-ended stream; pass the real byte count when known.
 */
export function buildWavHeader(
  sampleRate: number,
  numChannels = 1,
  bitsPerSample = 16,
  dataSize = 0xffffffff,
): Uint8Array {
  const header = new Uint8Array(44);
  const dv = new DataView(header.buffer);
  const writeStr = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++)
      dv.setUint8(offset + i, str.charCodeAt(i));
  };
  const byteRate = (sampleRate * numChannels * bitsPerSample) / 8;
  const blockAlign = (numChannels * bitsPerSample) / 8;
  const riffSize = dataSize === 0xffffffff ? 0xffffffff : dataSize + 36;

  writeStr(0, "RIFF");
  dv.setUint32(4, riffSize, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  dv.setUint32(16, 16, true); // fmt chunk size
  dv.setUint16(20, 1, true); // PCM
  dv.setUint16(22, numChannels, true);
  dv.setUint32(24, sampleRate, true);
  dv.setUint32(28, byteRate, true);
  dv.setUint16(32, blockAlign, true);
  dv.setUint16(34, bitsPerSample, true);
  writeStr(36, "data");
  dv.setUint32(40, dataSize, true);
  return header;
}

/**
 * Streaming linear resampler. The mic runs at the AudioContext's native rate
 * (44.1/48 kHz) but the gateway's realtime path only accepts 24 kHz PCM16 —
 * sending native-rate samples there yields sped-up audio and garbled text.
 *
 * State is kept across calls (fractional read position + the trailing sample)
 * so block boundaries interpolate continuously instead of clicking.
 */
export function createResampler(
  fromRate: number,
  toRate: number,
): (input: Float32Array) => Float32Array {
  if (fromRate === toRate) return (input) => input;
  const ratio = fromRate / toRate;
  let carry = new Float32Array(0);
  let pos = 0;

  return (input) => {
    let buf: Float32Array;
    if (carry.length) {
      buf = new Float32Array(carry.length + input.length);
      buf.set(carry, 0);
      buf.set(input, carry.length);
    } else {
      buf = input;
    }

    const outCount = Math.max(0, Math.ceil((buf.length - 1 - pos) / ratio));
    const out = new Float32Array(outCount);
    let p = pos;
    let n = 0;
    while (Math.floor(p) + 1 < buf.length && n < outCount) {
      const i0 = Math.floor(p);
      const frac = p - i0;
      out[n++] = buf[i0] * (1 - frac) + buf[i0 + 1] * frac;
      p += ratio;
    }

    // Keep everything from the next read index on for the following block.
    const keepFrom = Math.min(Math.floor(p), buf.length);
    carry = buf.slice(keepFrom);
    pos = p - keepFrom;
    return n === outCount ? out : out.subarray(0, n);
  };
}

/** Concatenate Uint8Array chunks into one buffer. */
export function concatChunks(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((n, c) => n + c.byteLength, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out;
}

/**
 * False only when the embedding page's Permissions-Policy explicitly disallows
 * the microphone. The widget mounts as Shadow DOM inside the host document, so
 * a host header like `Permissions-Policy: microphone=()` blocks getUserMedia
 * with a NotAllowedError and no prompt — the mic can never work on such a page,
 * and no browser-settings change by the user can override it.
 *
 * Returns true when the API is unavailable (older browsers): we can't prove a
 * block, so we don't hide the mic on a false negative.
 */
export function isMicAllowedByPolicy(): boolean {
  if (typeof document === "undefined") return true;
  const fp = (document as any).featurePolicy;
  if (fp && typeof fp.allowsFeature === "function") {
    try {
      return fp.allowsFeature("microphone");
    } catch {
      return true;
    }
  }
  return true;
}

/** True when the browser can capture raw PCM for streaming. */
export function isStreamingSupported(): boolean {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return false;
  }
  const AC = (window as any).AudioContext || (window as any).webkitAudioContext;
  return Boolean(
    navigator.mediaDevices?.getUserMedia &&
    AC &&
    typeof AudioWorkletNode !== "undefined" &&
    typeof WebSocket !== "undefined",
  );
}

function classifyGetUserMediaError(err: unknown): SttError {
  const name = (err as Error)?.name ?? "";
  let kind: SttErrorKind = "unknown";
  if (name === "NotAllowedError" || name === "SecurityError") {
    kind = "permission-denied";
  } else if (
    name === "NotFoundError" ||
    name === "OverconstrainedError" ||
    name === "NotReadableError"
  ) {
    kind = "no-device";
  }
  return new SttError(kind, (err as Error)?.message);
}

const WORKLET_SRC = `
class FsPcmProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      // Copy — the underlying buffer is reused across render quanta.
      this.port.postMessage(input[0].slice(0));
    }
    return true;
  }
}
registerProcessor('fs-pcm-processor', FsPcmProcessor);
`;

export interface PcmRecorderOptions {
  /** Called with batched little-endian PCM16 bytes (~flushIntervalMs apart). */
  onChunk: (pcm: Uint8Array) => void;
  /** Desired capture rate; the browser may pick another (read `sampleRate`). */
  sampleRate?: number;
  /**
   * Resample the captured audio to this rate before emitting. Required for the
   * gateway's realtime path (24 kHz). Capture still happens at the mic's native
   * rate — forcing the AudioContext rate makes Safari emit silence — and
   * `sampleRate` reports the rate actually emitted.
   */
  targetSampleRate?: number;
  /** How often to flush accumulated samples. Defaults to 250ms. */
  flushIntervalMs?: number;
}

export interface PcmRecorderHandle {
  /** Request mic permission and begin capturing. Rejects with {@link SttError}. */
  start: () => Promise<void>;
  /** Flush any remaining samples and release the mic. */
  stop: () => void;
  /** Abort without flushing and release the mic. */
  cancel: () => void;
  /** Actual capture sample rate (known after start). */
  readonly sampleRate: number;
}

export function createPcmRecorder(
  options: PcmRecorderOptions,
): PcmRecorderHandle {
  const {
    onChunk,
    sampleRate = 16000,
    targetSampleRate,
    flushIntervalMs = 250,
  } = options;

  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let node: AudioWorkletNode | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let pending: Uint8Array[] = [];
  let flushTimer: ReturnType<typeof setInterval> | null = null;
  let actualRate = sampleRate;
  let resample: (input: Float32Array) => Float32Array = (input) => input;

  const flush = () => {
    if (pending.length === 0) return;
    const merged = concatChunks(pending);
    pending = [];
    onChunk(merged);
  };

  const teardown = () => {
    if (flushTimer) clearInterval(flushTimer);
    flushTimer = null;
    try {
      node?.disconnect();
      source?.disconnect();
    } catch {
      /* ignore */
    }
    stream?.getTracks().forEach((t) => t.stop());
    void ctx?.close().catch(() => {});
    node = null;
    source = null;
    stream = null;
    ctx = null;
  };

  return {
    get sampleRate() {
      return actualRate;
    },
    async start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        throw classifyGetUserMediaError(err);
      }
      const AC =
        (window as any).AudioContext || (window as any).webkitAudioContext;
      // Don't force a sample rate: Safari's createMediaStreamSource emits
      // silence when the context rate differs from the mic's native rate, so
      // the clip comes back empty and the gateway rejects it (surfacing as a
      // generic "couldn't reach the service" error). Capture at the native
      // rate — the WAV header carries `actualRate`, so downstream stays correct.
      ctx = new AC();
      const captureRate = ctx!.sampleRate;
      // Emit at the target rate when one is requested (realtime needs 24 kHz);
      // otherwise pass the native samples through untouched.
      if (targetSampleRate && targetSampleRate !== captureRate) {
        resample = createResampler(captureRate, targetSampleRate);
        actualRate = targetSampleRate;
      } else {
        actualRate = captureRate;
      }
      const blob = new Blob([WORKLET_SRC], { type: "application/javascript" });
      const url = URL.createObjectURL(blob);
      try {
        await ctx!.audioWorklet.addModule(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      source = ctx!.createMediaStreamSource(stream);
      node = new AudioWorkletNode(ctx!, "fs-pcm-processor");
      node.port.onmessage = (e: MessageEvent) => {
        const samples = resample(e.data as Float32Array);
        if (samples.length) pending.push(floatToPcm16(samples));
      };
      source.connect(node);
      // The processor emits no output, so routing to destination stays silent
      // while keeping the graph pulling audio in every browser.
      node.connect(ctx!.destination);
      // Safari creates the context "suspended" and won't pull audio until
      // resumed; Chrome auto-resumes on connect. Resume explicitly so the mic
      // actually flows on every browser.
      if (ctx!.state === "suspended") {
        try {
          await ctx!.resume();
        } catch {
          /* ignore — capture may still work */
        }
      }
      flushTimer = setInterval(flush, flushIntervalMs);
    },
    stop() {
      flush();
      teardown();
    },
    cancel() {
      pending = [];
      teardown();
    },
  };
}
