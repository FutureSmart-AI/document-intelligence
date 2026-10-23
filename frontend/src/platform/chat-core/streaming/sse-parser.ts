import { decodeEscapeChars } from "../utils/decode";
import { safeParseJson } from "../utils/json";
import type { ParsedSSEEvent, SSEError, SSEParserConfig } from "./types";

/**
 * Framework-agnostic SSE stream parser.
 * Handles data: line splitting, JSON buffering for incomplete chunks,
 * escape character decoding, and throttled event emission.
 */
export class SSEParser {
  private config: Required<SSEParserConfig>;
  private lineBuffer = ""; // buffers incomplete lines across processChunkString calls
  private updateTimeout: ReturnType<typeof setTimeout> | null = null;
  private latestEvent: ParsedSSEEvent | null = null;
  private abortController: AbortController | null = null;
  private timeoutId: ReturnType<typeof setTimeout> | null = null;

  constructor(config: SSEParserConfig) {
    this.config = {
      timeoutMs: config.timeoutMs ?? 90000,
      throttleMs: config.throttleMs ?? 50,
      onChunk: config.onChunk,
      onComplete: config.onComplete,
      onError: config.onError,
    };
  }

  /**
   * Returns an AbortSignal that can be passed to fetch().
   * The parser manages its own AbortController for timeout handling.
   */
  createAbortSignal(): AbortSignal {
    this.abortController = new AbortController();
    this.armTimeout();
    return this.abortController.signal;
  }

  /**
   * (Re)arms the idle timeout. Called at connect and again on every received
   * chunk so an actively-streaming response is never aborted mid-flight — only
   * a genuine stall (no data for `timeoutMs`) triggers the abort.
   */
  private armTimeout(): void {
    if (this.timeoutId) clearTimeout(this.timeoutId);
    this.timeoutId = setTimeout(() => {
      this.abortController?.abort();
    }, this.config.timeoutMs);
  }

  /**
   * Processes a raw ReadableStream from a fetch response.
   */
  async processStream(
    reader: ReadableStreamDefaultReader<Uint8Array>,
  ): Promise<void> {
    const decoder = new TextDecoder("utf-8");

    try {
      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          this.flush();
          this.config.onComplete();
          return;
        }

        const chunkString = decoder.decode(value);
        this.processChunkString(chunkString);
      }
    } catch (error: any) {
      const sseError = this.classifyError(error);
      this.config.onError(sseError);
    } finally {
      this.cleanup();
    }
  }

  /**
   * Processes a raw string chunk from the SSE stream.
   * Can be called directly if you manage the reader yourself.
   */
  processChunkString(chunkString: string): void {
    // Data arrived — the stream is alive, so push the idle deadline forward.
    if (this.timeoutId) this.armTimeout();

    // Check for quota/rate limit errors
    if (
      chunkString.includes("insufficient_quota") ||
      chunkString.includes("Error code: 429")
    ) {
      const parsedData = safeParseJson(chunkString);
      if (parsedData?.detail) {
        this.config.onError({
          type: "quota",
          message: parsedData.detail,
          original: parsedData,
        });
        return;
      }
    }

    // Use a line-buffer approach: accumulate until '\n', then process lines
    // that start with "data:". This correctly handles large SSE events whose
    // JSON payload itself contains the substring "data:" (e.g. {"data": {...}}).
    const raw = this.lineBuffer + chunkString;
    const lines = raw.split("\n");
    this.lineBuffer = lines.pop() ?? ""; // last element may lack a trailing \n

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const jsonString = trimmed.slice(5).trim(); // strip "data:" prefix
      if (!jsonString || jsonString === "[DONE]") continue;
      const parsed = safeParseJson(jsonString);
      if (parsed) {
        this.emitParsedEvent(parsed);
      }
    }
  }

  /**
   * Flushes any line still in the buffer (call after the stream ends).
   * Also flushes any pending throttled event.
   */

  private emitParsedEvent(parsedData: any): void {
    const event: ParsedSSEEvent = {};

    const chunk = parsedData.chunk;
    const response = parsedData.response;

    if (response !== undefined && response !== null) {
      event.response = decodeEscapeChars(response);
    } else if (chunk !== undefined && chunk !== null) {
      event.chunk = decodeEscapeChars(chunk);
    }

    if (parsedData.debug_info != null) event.debug_info = parsedData.debug_info;
    if (parsedData.chat_id != null) event.chat_id = parsedData.chat_id;
    if (parsedData.session_id != null) event.session_id = parsedData.session_id;
    if (parsedData.follow_up != null) event.follow_up = parsedData.follow_up;
    if (parsedData.data != null) event.data = parsedData.data;

    this.latestEvent = event;
    this.scheduleEmit();
  }

  private scheduleEmit(): void {
    if (this.updateTimeout) return;
    this.updateTimeout = setTimeout(() => {
      this.updateTimeout = null;
      if (this.latestEvent) {
        this.config.onChunk(this.latestEvent);
        this.latestEvent = null;
      }
    }, this.config.throttleMs);
  }

  flush(): void {
    // Drain any incomplete line still in the buffer
    if (this.lineBuffer.trim()) {
      this.processChunkString("\n"); // force a newline to flush the buffered line
    }
    if (this.updateTimeout) {
      clearTimeout(this.updateTimeout);
      this.updateTimeout = null;
    }
    if (this.latestEvent) {
      this.config.onChunk(this.latestEvent);
      this.latestEvent = null;
    }
  }

  /**
   * Cleans up timers and abort controller.
   */
  cleanup(): void {
    if (this.timeoutId) {
      clearTimeout(this.timeoutId);
      this.timeoutId = null;
    }
    if (this.updateTimeout) {
      clearTimeout(this.updateTimeout);
      this.updateTimeout = null;
    }
    this.lineBuffer = "";
    this.latestEvent = null;
  }

  /**
   * Aborts the stream.
   */
  abort(): void {
    this.abortController?.abort();
    this.cleanup();
  }

  private classifyError(error: any): SSEError {
    if (error?.name === "AbortError") {
      return {
        type: "timeout",
        message: "Request timed out. Please try again.",
        original: error,
      };
    }
    if (error?.response?.data?.detail) {
      return {
        type: "quota",
        message: error.response.data.detail,
        original: error,
      };
    }
    return {
      type: "unknown",
      message: "An error occurred. Please try again later.",
      original: error,
    };
  }
}
