"use client";

import { useCallback, useRef, useState } from "react";

import {
  defaultTo,
  get,
  isEmpty,
  isNil,
  isPlainObject,
  map,
  merge,
} from "lodash-es";

import {
  describeStreamFailure,
  streamSSERequest,
} from "@/platform/chat-core/streaming";
import type { Message } from "@/platform/chat-core/types";
import {
  decodeEscapeChars,
  processChunks,
  processSources,
  safeParseJson,
} from "@/platform/chat-core/utils";
import { useToast } from "@/platform/components/ToastContext";
import { baseUrl } from "@/platform/config";

/**
 * Where the questions go and how the stream is read.
 *
 * This product has exactly one: `knowledgeChatTransport` in
 * `@/data/knowledgeChat`, which posts to the knowledge service's own chat route
 * and renames its evidence fields into the shape this hook understands.
 * Accumulating chunks, sources, feedback ids and error handling stay here.
 */
export interface ChatStreamTransport {
  /** Absolute or `baseUrl`-relative path to POST the question to. */
  url: string;
  /** Extra SSE line prefixes to read besides `data:`. */
  eventPrefixes?: string[];
  /** Rewrites the caller's chat-service payload for this service. */
  buildBody?: (payload: Record<string, any>) => Record<string, any>;
  /** Rewrites one parsed event into the fields this hook understands. */
  adaptEvent?: (event: Record<string, any>) => Record<string, any>;
  /** Reads a stored session, answering `{ conversation_data }`. */
  fetchSession: (
    sessionId: string,
  ) => Promise<{ conversation_data: unknown[] }>;
  /**
   * Whether a finished answer is reconciled against the stored thread.
   *
   * The NL2SQL service resolves `debug_info` from the agent's own configuration
   * — which defaults to off — so a live answer arrives with no SQL, no tables
   * and no rows, while the thread it was written to has all three. Without this
   * the reader had to reload the page to see how an answer was reached. Only
   * the diagnostics are taken; the streamed text stays exactly as it streamed.
   */
  reconcileAfterStream?: boolean;
}

export const useChatStream = (transport: ChatStreamTransport) => {
  const { toast } = useToast();
  const [sessionMessages, setSessionMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [chatId, setChatId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = useRef<HTMLDivElement | null>(null);
  const isStreamingRef = useRef<boolean>(false);
  const justFinishedStreamingRef = useRef<boolean>(false);

  const scrollToBottom = useCallback(() => {
    if (justFinishedStreamingRef.current) return;
    if (messagesEndRef.current) {
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
      });
    }
  }, []);

  const scrollUserMessageToTop = useCallback(() => {
    if (!messagesContainerRef.current) return;
    const container = messagesContainerRef.current;
    const userMessages = container.querySelectorAll(
      '[data-message-role="user"]',
    );
    if (userMessages.length === 0) return;
    const lastUserMessage = userMessages[
      userMessages.length - 1
    ] as HTMLElement;
    if (lastUserMessage) {
      requestAnimationFrame(() => {
        if (messagesContainerRef.current && lastUserMessage) {
          const containerRect =
            messagesContainerRef.current.getBoundingClientRect();
          const messageRect = lastUserMessage.getBoundingClientRect();
          const scrollTop = messagesContainerRef.current.scrollTop;
          const targetScrollTop =
            scrollTop + messageRect.top - containerRect.top;
          messagesContainerRef.current.scrollTo({
            top: targetScrollTop,
            behavior: "smooth",
          });
        }
      });
    }
  }, []);

  const scrollStreamingResponseIntoView = useCallback(() => {
    if (!messagesContainerRef.current || !isStreamingRef.current) return;
    const container = messagesContainerRef.current;
    const userMessages = container.querySelectorAll(
      '[data-message-role="user"]',
    );
    if (userMessages.length > 0) {
      const lastUserMessage = userMessages[
        userMessages.length - 1
      ] as HTMLElement;
      if (lastUserMessage) {
        const containerRect = container.getBoundingClientRect();
        const userMessageTop =
          lastUserMessage.getBoundingClientRect().top - containerRect.top;
        if (Math.abs(userMessageTop) <= 5) return;
      }
    }
    const assistantMessages = container.querySelectorAll(
      '[data-message-role="assistant"]',
    );
    if (assistantMessages.length === 0) return;
    const lastAssistantMessage = assistantMessages[
      assistantMessages.length - 1
    ] as HTMLElement;
    if (lastAssistantMessage) {
      requestAnimationFrame(() => {
        if (messagesContainerRef.current && lastAssistantMessage) {
          const c = messagesContainerRef.current;
          const containerRect = c.getBoundingClientRect();
          const messageBottom =
            lastAssistantMessage.getBoundingClientRect().bottom -
            containerRect.top;
          if (messageBottom > c.clientHeight) {
            c.scrollTo({
              top: c.scrollTop + (messageBottom - c.clientHeight * 0.6),
              behavior: "auto",
            });
          }
        }
      });
    }
  }, []);

  const buildFinalSources = (mergedData: any, debugInfoContent: any) => {
    const sourcesFromData = get(mergedData, "knowledge.sources");
    const chunksFromData = get(mergedData, "knowledge.chunks");
    const summariesFromData = get(mergedData, "knowledge.summaries");
    const sourcesFromDebug = get(debugInfoContent, "sources");

    let finalSources =
      !isNil(sourcesFromData) && sourcesFromData.length > 0
        ? sourcesFromData
        : !isNil(chunksFromData) && chunksFromData.length > 0
          ? processChunks(chunksFromData, summariesFromData || {})
          : null;

    if (!finalSources && !isNil(sourcesFromDebug))
      finalSources = sourcesFromDebug;
    if (finalSources) finalSources = processSources(finalSources);
    return finalSources;
  };

  /**
   * A stored thread, mapped into the shape this hook renders.
   *
   * `replace: false` reads it without touching what is on screen, which is how
   * a just-answered turn is reconciled against the store (see `callStreamAnswer`).
   */
  const fetchSessionDetails = async (
    existingSessionId: string,
    { replace = true }: { replace?: boolean } = {},
  ) => {
    try {
      if (replace) {
        setLoading(true);
        setError(null);
        setSessionMessages([]);
        setSessionId("");
        setChatId("");
      }

      // A product that owns its own sessions reads them from its own service —
      // a session opened by the NL2SQL service is not in the dashboard store, so
      // asking there for it returned an empty thread.
      const data = await transport.fetchSession(existingSessionId);

      const conversationData = defaultTo(get(data, "conversation_data"), []);
      const mappedMessages = map(conversationData, (message: Message) => {
        const parsedData = safeParseJson(get(message, "debug_info"));
        const chartOutput = safeParseJson(get(message, "chart_output"));
        let messageData = defaultTo(
          get(message, "data"),
          defaultTo(get(parsedData, "data"), null),
        );

        // Session API splits the live `data.nl2sql` payload: sql/tables/db_result
        // come back in debug_info.nl2sql, chart metadata in chart_output.nl2sql.
        // Merge them back into the live shape so normalizeNl2sql + NL2SQLPanel
        // render identically with no component-side logic.
        const nl2sqlFromDebug = get(parsedData, "nl2sql");
        const nl2sqlFromChart = get(chartOutput, "nl2sql");
        const hasNl2sql =
          isPlainObject(nl2sqlFromDebug) || isPlainObject(nl2sqlFromChart);
        if (hasNl2sql && isNil(get(messageData, "nl2sql"))) {
          messageData = {
            ...(isPlainObject(messageData) ? messageData : {}),
            nl2sql: merge({}, nl2sqlFromDebug, nl2sqlFromChart),
          };
        }

        const hasDataKnowledge =
          messageData && !isEmpty(get(messageData, "knowledge"));
        const sourcesFromData = get(messageData, "knowledge.sources");
        const chunksFromData = get(messageData, "knowledge.chunks");
        const summariesFromData = get(messageData, "knowledge.summaries");
        const sourcesFromDebug = get(parsedData, "sources");

        let finalSources = !isNil(sourcesFromData)
          ? sourcesFromData
          : !isNil(chunksFromData) && chunksFromData.length > 0
            ? processChunks(chunksFromData, summariesFromData || {})
            : defaultTo(sourcesFromDebug, null);

        if (finalSources) finalSources = processSources(finalSources);

        return {
          chat_id: get(message, "chat_id"),
          role: get(message, "role"),
          // Carried through for the run log, where a stored turn reports one.
          trace_id: get(message, "trace_id") ?? get(parsedData, "trace_id"),
          is_answered: get(message, "is_answered"),
          session_id: get(message, "session_id") ?? existingSessionId,
          message: decodeEscapeChars(get(message, "message") || ""),
          sources: finalSources,
          chart_output: chartOutput,
          debug_info: hasDataKnowledge ? undefined : parsedData,
          user_feedback: get(message, "user_feedback"),
          data: messageData,
        };
      });

      if (replace) {
        setSessionMessages(mappedMessages);
        setSessionId(existingSessionId);
        scrollToBottom();
      }
      return mappedMessages;
    } catch (error: any) {
      // A background read must stay silent: the answer it was enriching is on
      // screen and correct, and a toast about a thread the reader never asked
      // for would be a failure they cannot act on.
      if (!replace) throw error;
      const errorMessage = defaultTo(
        get(error, "response.data.message"),
        "Failed to fetch session details",
      );
      setError(errorMessage);
      toast.error(errorMessage);
      throw error;
    } finally {
      if (replace) setLoading(false);
    }
  };

  /**
   * Fills a finished answer's diagnostics from the thread it was written to.
   *
   * Enabled per product by `reconcileAfterStream`. The stored turn is read
   * without touching the screen and merged *under* what the stream reported, so
   * the answer text, its sources and its feedback ids are the streamed ones and
   * only the gaps — the SQL, the tables, the rows — are filled. A thread the
   * service has not written yet, or cannot be read, leaves the turn alone: the
   * answer on screen is still the answer.
   */
  const reconcileWithStore = async (sessionIdToRead: string | null) => {
    if (!transport.reconcileAfterStream) return;
    if (!sessionIdToRead) return;
    let stored: Message[] = [];
    try {
      stored = await fetchSessionDetails(sessionIdToRead, { replace: false });
    } catch {
      return;
    }
    const answers = stored.filter(
      (entry) => get(entry, "role") === "assistant",
    );
    const lastStored = answers[answers.length - 1];
    if (!lastStored) return;
    setSessionMessages((prev) => {
      const index = prev.length - 1;
      if (index < 0 || get(prev[index], "role") !== "assistant") return prev;
      const current = prev[index];
      const currentSources = get(current, "sources") as unknown[] | undefined;
      const updated = [...prev];
      /**
       * The stored turn fills the gaps, but never per row.
       *
       * Both turns describe the same run, and a deep merge fused them cell by
       * cell: the stored thread keeps the rows positionally (`column_1`,
       * `column_2` — see `parseDbOutput`), the live turn keeps them by name,
       * and merging the two arrays index by index gave every row both sets of
       * keys. The card then drew a phantom `COLUMN_2` beside `COUNT`, holding
       * the same numbers. Keyed one level down instead, the live block wins
       * per field and the stored one still supplies what the live turn never
       * sent.
       */
      const mergedData = merge(
        {},
        get(lastStored, "data"),
        get(current, "data"),
      );
      const storedBlock = get(lastStored, ["data", "nl2sql"]);
      const liveBlock = get(current, ["data", "nl2sql"]);
      if (isPlainObject(storedBlock) && isPlainObject(liveBlock)) {
        mergedData.nl2sql = { ...storedBlock, ...liveBlock };
      }
      updated[index] = {
        ...current,
        data: mergedData,
        sources: currentSources?.length ? current.sources : lastStored.sources,
        debug_info: current.debug_info ?? lastStored.debug_info,
        chat_id: current.chat_id || lastStored.chat_id,
      };
      return updated;
    });
  };

  const callStreamAnswer = async (payload: any) => {
    // Accumulated state — lives outside React during the stream
    let botResponse = "";
    let debugInfoContent: any = null;
    let followUpQuestions: any = null;
    let currentChatId: any = null;
    let responseData: any = null;
    /** Set when the stream reports the run failed; see `onEvent`. */
    let streamFailure: string | null = null;
    /**
     * The run's own id and its own verdict on the turn.
     *
     * Both ride the final metadata event and neither used to survive onto the
     * message, so the run log had no trace id to show and no way to know the
     * service had reported `is_answered: false`.
     */
    let traceId: string | null = null;
    let isAnswered: boolean | null = null;
    let answeredSessionForTurn: string | null = null;
    /** The thread this answer belongs to, for the reconcile below. */
    let answeredSessionId: string | null = payload?.session_id || null;

    const UPDATE_THROTTLE_MS = 50;
    let updateTimeout: ReturnType<typeof setTimeout> | null = null;

    const scheduleUpdate = () => {
      if (updateTimeout) return;
      updateTimeout = setTimeout(() => {
        updateTimeout = null;
        setSessionMessages((prev) => {
          const updated = [...prev];
          const lastIndex = updated.length - 1;
          if (lastIndex >= 0 && updated[lastIndex]?.role === "assistant") {
            const existingData = get(updated[lastIndex], "data");
            const mergedData = responseData
              ? existingData
                ? merge({}, existingData, responseData)
                : responseData
              : existingData;
            const hasDataKnowledge =
              mergedData && !isEmpty(get(mergedData, "knowledge"));
            updated[lastIndex] = {
              ...updated[lastIndex],
              role: "assistant",
              message: botResponse,
              sources: buildFinalSources(mergedData, debugInfoContent),
              debug_info: hasDataKnowledge ? undefined : debugInfoContent,
              follow_up: followUpQuestions,
              chat_id: currentChatId,
              trace_id: traceId,
              is_answered: isAnswered,
              session_id: answeredSessionForTurn,
              data: mergedData,
            };
          }
          return updated;
        });
        scrollStreamingResponseIntoView();
      }, UPDATE_THROTTLE_MS);
    };

    try {
      setError(null);
      isStreamingRef.current = true;
      justFinishedStreamingRef.current = false;

      const { sessionId: newSessionId } = await streamSSERequest({
        url: `${baseUrl}${transport.url}`,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          accept: "text/event-stream",
        },
        credentials: "include",
        body: JSON.stringify(
          transport.buildBody ? transport.buildBody(payload) : { ...payload },
        ),
        eventPrefixes: transport.eventPrefixes,
        onEvent: (rawEvent) => {
          const event = transport.adaptEvent
            ? transport.adaptEvent(rawEvent as Record<string, any>)
            : rawEvent;
          const {
            chunk,
            response,
            debug_info,
            session_id,
            follow_up,
            chat_id,
            data,
            error: streamError,
          } = event;

          // A run that fails after the stream opened still answers 200 and ends
          // normally, reporting itself as `{"error": …}`. Without this the turn
          // finished as an empty assistant bubble with nothing to explain it —
          // the answer simply never appeared.
          if (typeof streamError === "string" && streamError.trim()) {
            streamFailure = streamError;
            return;
          }

          if (!isNil(response)) {
            botResponse = decodeEscapeChars(response);
          } else if (!isNil(chunk)) {
            botResponse += decodeEscapeChars(chunk);
          }
          if (!isNil(debug_info)) debugInfoContent = debug_info;
          if (!isNil(event.trace_id)) traceId = event.trace_id;
          if (!isNil(session_id)) answeredSessionForTurn = session_id;
          if (typeof event.is_answered === "boolean") {
            isAnswered = event.is_answered;
          }
          if (!isNil(chat_id)) {
            currentChatId = chat_id;
            setChatId(chat_id);
          }
          if (!isNil(session_id)) {
            answeredSessionId = session_id;
            setSessionId(session_id);
          }
          if (!isNil(follow_up)) followUpQuestions = follow_up;
          if (!isNil(data)) {
            responseData = responseData ? merge({}, responseData, data) : data;
          }
          scheduleUpdate();
        },
        onComplete: () => {
          if (updateTimeout) {
            clearTimeout(updateTimeout);
            updateTimeout = null;
          }

          // A reported failure is shown the same way a transport failure is:
          // in the error banner and as a toast. What the run already produced
          // is left alone — an NL2SQL run whose chart step breaks has already
          // streamed the answer, the SQL and the rows, and those still belong
          // in the thread. Only a run that produced nothing puts the failure in
          // the bubble, so the turn is never a silent empty answer.
          //
          // The reported text is not shown as-is: a step that breaks reports
          // the raw exception ("'NoneType' object has no attribute 'get'"),
          // which means nothing to a user.
          let failureMessage: string | null = null;
          if (streamFailure) {
            failureMessage = describeStreamFailure(streamFailure);
            if (process.env.NODE_ENV === "development") {
              console.error("Chat stream reported a failure:", streamFailure);
            }
            setError(failureMessage);
            toast.error(failureMessage);
          }

          setSessionMessages((prev) => {
            const updated = [...prev];
            const existingData = get(updated[updated.length - 1], "data");
            const mergedData = responseData
              ? existingData
                ? merge({}, existingData, responseData)
                : responseData
              : existingData;
            const hasDataKnowledge =
              mergedData && !isEmpty(get(mergedData, "knowledge"));
            updated[updated.length - 1] = {
              role: "assistant",
              message: botResponse || failureMessage || "",
              sources: buildFinalSources(mergedData, debugInfoContent),
              debug_info: hasDataKnowledge ? undefined : debugInfoContent,
              follow_up: followUpQuestions,
              chat_id: currentChatId ?? "",
              trace_id: traceId,
              is_answered: isAnswered,
              session_id: answeredSessionForTurn,
              data: mergedData,
            };
            return updated;
          });
          isStreamingRef.current = false;
          justFinishedStreamingRef.current = true;
          setTimeout(() => {
            justFinishedStreamingRef.current = false;
          }, 1000);
          // The answer is on screen either way; this only fills what the live
          // stream was not configured to send.
          void reconcileWithStore(answeredSessionId);
        },
        onError: (sseError) => {
          const errorMessage =
            sseError.type === "timeout"
              ? "Request timed out. Please try again."
              : sseError.type === "quota"
                ? sseError.message
                : "An error occurred. Please try again later.";

          setError(errorMessage);
          // A quota message belongs where the answer would have gone and
          // nowhere else: it is a paragraph naming the ceiling and when the
          // reader can ask again, it stays on screen to be read, and a toast
          // saying the same thing twice is one of them dismissing itself. Every
          // other failure still toasts.
          if (sseError.type !== "quota") toast.error(errorMessage);
          setSessionMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = {
              role: "assistant",
              message: errorMessage,
              sources: [],
              follow_up: [],
              chat_id: "",
            };
            return updated;
          });
          isStreamingRef.current = false;
          justFinishedStreamingRef.current = true;
          setTimeout(() => {
            justFinishedStreamingRef.current = false;
          }, 1000);
        },
      });

      if (newSessionId) setSessionId(newSessionId);
    } catch (error: any) {
      const errorMessage = defaultTo(
        get(error, ["response", "data", "detail"]),
        "An error occurred. Please try again later.",
      );
      setError(errorMessage);
      toast.error(errorMessage);
      setSessionMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          role: "assistant",
          message: errorMessage,
          sources: [],
          follow_up: [],
          chat_id: "",
        };
        return updated;
      });
    } finally {
      isStreamingRef.current = false;
    }
  };

  const addMessage = (message: string, viaVoice = false) => {
    justFinishedStreamingRef.current = false;
    setSessionMessages((prev) => [
      ...prev,
      { role: "user", message, ...(viaVoice ? { via_voice: true } : {}) },
      {
        role: "assistant",
        message: "Thinking...",
        sources: [],
        follow_up: [],
        chat_id: "",
      },
    ]);
    setTimeout(() => {
      scrollUserMessageToTop();
    }, 100);
  };

  const resetChat = useCallback(() => {
    setSessionMessages([]);
    setSessionId("");
    setChatId("");
    setError(null);
    setLoading(false);
  }, []);

  return {
    sessionMessages,
    sessionId,
    messagesEndRef,
    messagesContainerRef,
    loading,
    chatId,
    error,
    fetchSessionDetails,
    callStreamAnswer,
    addMessage,
    scrollToBottom,
    resetChat,
  };
};
