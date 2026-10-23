import { memo, useMemo, useState } from "react";

import { defaultTo, get, isEmpty } from "lodash-es";
import { Bot, ChevronDown, Code2, Mic, User } from "lucide-react";

import { ChatChart } from "@/platform/chat-core/charts";
import { slateSourcesTheme, SourcesPanel } from "@/platform/chat-core/sources";
import {
  createSynthesizer,
  toPlainText,
  ttsErrorMessage,
  useTextToSpeech,
} from "@/platform/chat-core/tts";
import { ttsEnabled, ttsUrl } from "@/platform/config";
import { useSources } from "@/platform/hooks/useSources";
import { cn } from "@/platform/utils";

import { useToast } from "../ToastContext";
import Button from "../ui/Button";
import ChatFeedbackButtons from "./ChatFeedbackButtons";
import { CopyToClipboard } from "./copy-to-clipboard";
import { normalizeKnowledgeChart } from "./knowledge/normalizeKnowledgeChart";
import { Markdown } from "./markdown";
import { normalizeNl2sql } from "./nl2sql/normalizeNl2sql";
import NL2SQLPanel from "./NL2SQLPanel";
import { ReadAloudButton } from "./ReadAloudButton";
import SourcesList from "./SourcesList";

interface ChatMessageProps {
  message: any;
  precedingMessage?: any;
  index: number;
  messageLoading: boolean;
  sessionId: string;
  chatId: string;
  activeTab: string;
  showSources: boolean;
  rawEnabled?: boolean;
  rawView?: boolean;
  readOnly?: boolean;
  /** Set when the thread belongs to the NL2SQL service rather than the chat service. */
  nl2sqlServiceId?: string | null;
  /** Set when the thread belongs to the knowledge service (Document Intelligence). */
  knowledgeId?: string | null;
  /** Explicit product setting; omitted preserves the chat registry behaviour. */
  voiceOutputEnabled?: boolean;
}

export const ChatMessage = memo(
  ({
    message,
    precedingMessage,
    index,
    messageLoading,
    sessionId,
    chatId,
    activeTab,
    showSources,
    rawEnabled = false,
    rawView,
    readOnly,
    nl2sqlServiceId,
    knowledgeId,
    voiceOutputEnabled,
  }: ChatMessageProps) => {
    const isUser = get(message, "role") === "user";
    const isAssistant = get(message, "role") === "assistant";

    // Raw view: pair the assistant's full, untouched response with the
    // query that produced it (preceding user message), like the batch
    // report's per-query JSON dump.
    const rawPayload = isAssistant
      ? {
          query: get(
            precedingMessage,
            "response",
            get(precedingMessage, "message", null),
          ),
          ...message,
        }
      : message;

    // Use response as primary field (new unified API), fallback to message for backward compatibility
    const responseContent = defaultTo(
      get(message, "response"),
      defaultTo(get(message, "message"), ""),
    );

    const isThinking =
      isAssistant &&
      (responseContent === "Thinking..." ||
        get(message, "message") === "Thinking...") &&
      messageLoading;

    const { toast } = useToast();
    const synthesize = useMemo(
      () => createSynthesizer({ baseUrl: ttsUrl, withCredentials: true }),
      [],
    );
    const tts = useTextToSpeech({
      synthesize,
      onError: (e) => toast.error(ttsErrorMessage(e.kind)),
    });
    const plainResponse = useMemo(
      () => toPlainText(String(responseContent ?? "")),
      [responseContent],
    );

    const sources = useSources(message);
    const [activePanel, setActivePanel] = useState<"sources" | "raw" | null>(
      null,
    );
    const renderMarkdown = (text: string) => <Markdown>{text}</Markdown>;

    const nl2sqlView = normalizeNl2sql(message);
    const showQuery = true;
    const showResults = true;

    const nl2sql =
      get(message, ["data", "nl2sql"], null) ||
      get(message, ["chart_output", "nl2sql"], null);

    const chart =
      get(message, ["data", "chart"], null) ||
      get(message, ["chart_output", "chart"], null);

    // Document Intelligence puts its chart on the evidence block instead:
    // `data.knowledge.chart_output`. See `knowledge/normalizeKnowledgeChart`.
    const knowledgeChart = normalizeKnowledgeChart(message);
    const hasChart = !!(nl2sql || chart || knowledgeChart);

    // Check if the message contains a markdown table
    const hasTable =
      responseContent &&
      typeof responseContent === "string" &&
      /\|.+\|/.test(responseContent);

    // Fenced code block — needs full-width bubble so the inner scroll
    // container can shrink to viewport and scroll horizontally on mobile.
    const hasCode =
      responseContent &&
      typeof responseContent === "string" &&
      /```/.test(responseContent);

    return (
      <div
        role="listitem"
        data-message-role={isUser ? "user" : "assistant"}
        data-message-index={index}
        className={`flex gap-2 sm:gap-4 max-w-[92%] sm:max-w-[80%] animate-in fade-in slide-in-from-bottom-2 duration-300 ${
          isUser ? "ml-auto flex-row-reverse" : "mr-auto"
        }`}
      >
        {/* Avatar */}
        <div
          className={`size-10 rounded-full flex items-center justify-center shrink-0 transition-all duration-300 hover:scale-110 ${
            isAssistant
              ? "bg-linear-to-br from-indigo-100 to-purple-100 text-indigo-700 ring-2 ring-indigo-200/50"
              : "bg-linear-to-br from-blue-500 to-indigo-600 text-white ring-2 ring-blue-400/30 shadow-lg"
          } shadow-md`}
        >
          {isAssistant ? (
            <Bot className="w-5 h-5" />
          ) : (
            <User className="w-5 h-5" />
          )}
        </div>

        {/* Bubble + raw panel column */}
        <div
          className={cn(
            "flex flex-col gap-3 min-w-0 flex-1",
            isUser && "items-end",
          )}
        >
          {/* Message bubble */}
          <div
            className={`rounded-2xl px-5 py-4 transition-all duration-200 ${
              isUser
                ? "bg-linear-to-br from-indigo-600 via-blue-600 to-indigo-700 text-white rounded-tr-md shadow-lg hover:shadow-xl hover:scale-[1.01] backdrop-blur-sm [&_a]:text-sky-200 [&_a]:underline [&_a]:underline-offset-2 [&_a]:decoration-sky-200/50 hover:[&_a]:text-white"
                : "bg-white text-slate-900 border border-slate-200/80 rounded-tl-md shadow-md hover:shadow-lg hover:border-indigo-200/60 backdrop-blur-sm"
            } ${rawView || hasChart || hasTable || hasCode ? "w-full min-w-0" : "w-fit max-w-full"}`}
          >
            {/* Loading / Thinking indicator */}
            {isThinking ? (
              <div className="flex items-center gap-3 text-slate-600">
                <span className="text-sm">Thinking</span>
                <span className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce [animation-delay:-0.2s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce [animation-delay:-0.1s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" />
                </span>
              </div>
            ) : rawView ? (
              <pre className="bg-[#1a1a2e] rounded-lg px-4 py-3.5 font-mono text-xs text-[#a8b5c8] leading-relaxed overflow-x-auto whitespace-pre max-w-full">
                {JSON.stringify(rawPayload, null, 2)}
              </pre>
            ) : (
              <>
                <Markdown>{responseContent}</Markdown>

                {/* NL2SQL */}
                {isAssistant && nl2sql && (
                  <ChatChart
                    chartData={nl2sql.chart_data}
                    chartSuggestion={nl2sql.chart_suggestion}
                  />
                )}

                {/* Chart */}
                {isAssistant && chart && (
                  <ChatChart
                    chartData={chart.chart_data}
                    chartSuggestion={chart.chart_suggestion}
                  />
                )}

                {/* Knowledge service (Document Intelligence) */}
                {isAssistant && knowledgeChart && (
                  <ChatChart
                    chartData={knowledgeChart.chartData}
                    chartSuggestion={knowledgeChart.chartSuggestion}
                  />
                )}

                {/* Sources + Raw data — buttons side by side, panels below */}
                {isAssistant && (
                  <div className="mt-4">
                    <div className="flex items-center gap-2 flex-wrap">
                      {showSources && !isEmpty(sources) && (
                        <SourcesList
                          sources={sources}
                          messageIndex={index}
                          isOpen={activePanel === "sources"}
                          onToggle={() =>
                            setActivePanel(
                              activePanel === "sources" ? null : "sources",
                            )
                          }
                          className=""
                        />
                      )}
                      {rawEnabled && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="group"
                          startIcon={
                            <div className="flex items-center gap-1.5">
                              <Code2 className="size-3.5 text-indigo-600 transition-colors group-hover:text-indigo-700" />
                              <ChevronDown
                                className={cn(
                                  "size-4 text-slate-600 transition-transform duration-200",
                                  activePanel === "raw" ? "rotate-180" : "",
                                )}
                              />
                            </div>
                          }
                          onClick={() =>
                            setActivePanel(activePanel === "raw" ? null : "raw")
                          }
                          title={
                            activePanel === "raw"
                              ? "Hide raw response"
                              : "Show raw response"
                          }
                          aria-expanded={activePanel === "raw"}
                        >
                          <span className="font-medium">Raw response</span>
                        </Button>
                      )}
                    </div>
                    {activePanel === "sources" &&
                      showSources &&
                      !isEmpty(sources) && (
                        <div className="mt-3" id={`sources-${index}`}>
                          <SourcesPanel
                            sources={sources}
                            theme={slateSourcesTheme}
                            renderMarkdown={renderMarkdown}
                          />
                        </div>
                      )}
                  </div>
                )}

                {/* NL2SQL panel */}
                {showSources && isAssistant && nl2sqlView && (
                  <NL2SQLPanel
                    view={nl2sqlView}
                    showQuery={showQuery}
                    showResults={showResults}
                  />
                )}
              </>
            )}

            {/* "via voice input" tag for dictated user messages */}
            {isUser && get(message, "via_voice") && (
              <div className="mt-1.5 flex items-center justify-end gap-1 text-[10px] text-indigo-200">
                <Mic className="size-3" aria-hidden="true" />
                via voice input
              </div>
            )}

            {/* Actions row */}
            {isAssistant && !isThinking && (
              <div
                data-testid="message-actions"
                className="mt-2 flex items-center gap-3 text-xs"
              >
                {responseContent && (
                  <CopyToClipboard className="text-slate-700 hover:text-slate-900 hover:no-underline w-fit">
                    {responseContent}
                  </CopyToClipboard>
                )}
                {(voiceOutputEnabled ?? ttsEnabled) && (
                  <ReadAloudButton
                    status={tts.status}
                    disabled={!plainResponse}
                    onPlay={() => tts.play(plainResponse)}
                    onPause={tts.pause}
                    onResume={tts.resume}
                  />
                )}
                <ChatFeedbackButtons
                  sessionId={sessionId}
                  chatId={defaultTo(get(message, "chat_id"), chatId)}
                  userFeedback={defaultTo(get(message, "user_feedback"), null)}
                  activeTab={activeTab}
                  readOnly={readOnly}
                  knowledgeId={knowledgeId}
                />
              </div>
            )}
          </div>

          {/* Raw panel — outside bubble so card size stays fixed */}
          {activePanel === "raw" && rawEnabled && (
            <pre className="bg-[#1a1a2e] rounded-lg px-4 py-3.5 font-mono text-xs text-[#a8b5c8] leading-relaxed overflow-x-auto whitespace-pre max-w-full">
              {JSON.stringify(rawPayload, null, 2)}
            </pre>
          )}
        </div>
      </div>
    );
  },
);
