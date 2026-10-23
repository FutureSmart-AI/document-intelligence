import { safeParseJson } from "../utils/json";
import type { ParsedSSEEvent, SSEError } from "./types";

export interface StreamSSEOptions {
  url: string;
  method?: string;
  headers?: Record<string, string>;
  body?: string;
  credentials?: RequestCredentials;
  /**
   * Idle timeout in milliseconds. Default: 90000.
   * The stream is aborted only if NO data arrives within this window. The timer
   * resets on every received chunk, so a slow-but-alive stream never times out —
   * once data starts flowing, the request stays open as long as it keeps coming.
   */
  timeoutMs?: number;
  /**
   * Line prefixes that carry an event payload. Defaults to `["data:"]`.
   *
   * The chat service streams every field under `data:`. The NL2SQL service
   * streams the answer under `data:` but the turn's metadata — `chat_id`,
   * `follow_up`, `debug_info`, `chart_output` — under `full_metadata:`, so a
   * caller reading that stream passes both and gets the whole turn.
   */
  eventPrefixes?: string[];
  /**
   * Called for every fully-parsed SSE event.
   * Unlike SSEParser, this fires for EVERY event — no throttling or deduplication —
   * so callers that accumulate incremental `chunk` tokens receive all of them.
   */
  onEvent: (event: ParsedSSEEvent) => void;
  onComplete: () => void;
  onError: (error: SSEError) => void;
}

/**
 * Fetches an SSE endpoint and calls onEvent for each complete parsed event.
 *
 * Uses a line-buffer approach: accumulate raw bytes until a newline is seen,
 * then process only lines that start with "data:". This correctly handles:
 *   - Network reads that split mid-line
 *   - Large JSON events whose payload itself contains the substring "data:"
 *     (e.g. `{"data": {"knowledge": ...}}`)
 *
 * Returns the session ID from the x-session-id response header, if present.
 */
export async function streamSSERequest({
  url,
  method = "POST",
  headers = {},
  body,
  credentials,
  timeoutMs = 90000,
  eventPrefixes = ["data:"],
  onEvent,
  onComplete,
  onError,
}: StreamSSEOptions): Promise<{ sessionId: string | null }> {
  const controller = new AbortController();
  // Idle timeout: armed at connect, then re-armed on every received chunk so an
  // actively-streaming response is never aborted mid-flight. Only a genuine
  // stall (no data for `timeoutMs`) triggers the abort.
  let timeoutId: ReturnType<typeof setTimeout> = setTimeout(
    () => controller.abort(),
    timeoutMs,
  );
  const resetTimeout = () => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  };

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: headers as HeadersInit,
      body,
      ...(credentials ? { credentials } : {}),
      signal: controller.signal,
    });
  } catch (error: any) {
    clearTimeout(timeoutId);
    onError(
      error?.name === "AbortError"
        ? {
            type: "timeout",
            message: "Request timed out. Please try again.",
            original: error,
          }
        : {
            type: "network",
            message: "Unable to connect. Please try again.",
            original: error,
          },
    );
    return { sessionId: null };
  }

  // Non-ok HTTP response: the body is an error payload, not an SSE stream.
  // Reading it as a stream would surface nothing, so handle it explicitly.
  if (!response.ok) {
    clearTimeout(timeoutId);
    let detail: string | undefined;
    try {
      const text = await response.text();
      detail = safeParseJson(text)?.detail;
    } catch {
      // ignore body-read failures — fall back to a generic message
    }
    if (response.status === 401 || response.status === 403) {
      onError({
        type: "forbidden",
        message: detail ?? "Access denied.",
        status: response.status,
        original: response,
      });
    } else if (response.status === 429) {
      // A rate or allowance limit. The service's `detail` names the ceiling and
      // says when the caller can ask again, so it is carried through verbatim as
      // a quota error rather than flattened into a generic failure — the
      // generic message would drop the one sentence the reader needs.
      onError({
        type: "quota",
        message:
          detail ??
          "This workspace has reached its request limit. Try again later.",
        status: response.status,
        original: response,
      });
    } else {
      onError({
        type: "unknown",
        message: detail ?? "An error occurred. Please try again later.",
        status: response.status,
        original: response,
      });
    }
    return { sessionId: null };
  }

  const sessionId = response.headers.get("x-session-id");
  const reader = response.body?.getReader();

  if (!reader) {
    clearTimeout(timeoutId);
    onComplete();
    return { sessionId };
  }

  const decoder = new TextDecoder("utf-8");
  // Buffers the tail of each read that has not yet received a newline.
  let lineBuffer = "";

  const processLine = (line: string) => {
    const trimmed = line.trim();
    const prefix = eventPrefixes.find((candidate) =>
      trimmed.startsWith(candidate),
    );
    if (!prefix) return;
    const jsonString = trimmed.slice(prefix.length).trim();
    if (!jsonString || jsonString === "[DONE]") return;
    const parsed = safeParseJson(jsonString);
    if (parsed) onEvent(parsed);
  };

  try {
    while (true) {
      const { done, value } = await reader.read();

      if (done) {
        // Flush any remaining buffered line
        if (lineBuffer.trim()) processLine(lineBuffer);
        clearTimeout(timeoutId);
        onComplete();
        break;
      }

      // Data arrived — the stream is alive, so push the idle deadline forward.
      resetTimeout();

      const chunkString = lineBuffer + decoder.decode(value);

      // Quota / rate-limit error detection (non-SSE error body)
      if (
        chunkString.includes("insufficient_quota") ||
        chunkString.includes("Error code: 429")
      ) {
        const parsed = safeParseJson(chunkString);
        if (parsed?.detail) {
          onError({ type: "quota", message: parsed.detail, original: parsed });
          break;
        }
      }

      // Split by newline. The last element may be an incomplete line — keep it
      // in lineBuffer for the next read.
      const lines = chunkString.split("\n");
      lineBuffer = lines.pop() ?? "";
      for (const line of lines) {
        processLine(line);
      }
    }
  } catch (error: any) {
    onError(
      error?.name === "AbortError"
        ? {
            type: "timeout",
            message: "Request timed out. Please try again.",
            original: error,
          }
        : error?.response?.data?.detail
          ? {
              type: "quota",
              message: error.response.data.detail,
              original: error,
            }
          : {
              type: "unknown",
              message: "An error occurred. Please try again later.",
              original: error,
            },
    );
  } finally {
    clearTimeout(timeoutId);
    reader.releaseLock();
  }

  return { sessionId };
}
