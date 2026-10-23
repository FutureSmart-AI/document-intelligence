"use client";

import { useMemo } from "react";

import { get } from "lodash-es";

import Icon from "@/components/Icon";
import {
  createSynthesizer,
  toPlainText,
  ttsErrorMessage,
  useTextToSpeech,
} from "@/platform/chat-core/tts";
import ChatFeedbackButtons from "@/platform/components/conversations/ChatFeedbackButtons";
import { CopyToClipboard } from "@/platform/components/conversations/copy-to-clipboard";
import { normalizeKnowledgeChart } from "@/platform/components/conversations/knowledge/normalizeKnowledgeChart";
import { Markdown } from "@/platform/components/conversations/markdown";
import { ReadAloudButton } from "@/platform/components/conversations/ReadAloudButton";
import { useToast } from "@/platform/components/ToastContext";
import { ttsEnabled, ttsUrl } from "@/platform/config";
import { useSources } from "@/platform/hooks/useSources";

import { readChartDataframe } from "./chartDataframe";
import { deriveTrace } from "./deriveTrace";
import DiResultCard, { type DiSource } from "./DiResultCard";
import DiRunLog from "./DiRunLog";
import DiRunTrace from "./DiRunTrace";
import { readRunLog } from "./runLog";
import { stripResultTables } from "./stripResultTables";

interface DiTurnBlockProps {
  message: Record<string, unknown>;
  question: string;
  questionViaVoice?: boolean;
  sessionId: string;
  chatId: string;
  activeTab: string;
  readOnly?: boolean;
  knowledgeId?: string | null;
  /** Whether the collection lets an answer show the documents behind it. */
  showSources: boolean;
  /** Whether the collection exposes the untouched API payload. */
  rawEnabled: boolean;
  voiceOutputEnabled?: boolean;
  streaming: boolean;
  error?: string | null;
}

const answerText = (message: Record<string, unknown>): string =>
  String(get(message, "response") ?? get(message, "message") ?? "");

/** One question and the answer under it, in the artifact's layout. */
export default function DiTurnBlock({
  message,
  question,
  questionViaVoice,
  sessionId,
  chatId,
  activeTab,
  readOnly,
  knowledgeId,
  showSources,
  rawEnabled,
  voiceOutputEnabled,
  streaming,
  error,
}: DiTurnBlockProps) {
  const { toast } = useToast();
  const answer = answerText(message);
  // "Thinking..." is the placeholder the stream seeds an assistant turn with.
  const body = answer === "Thinking..." ? "" : answer;

  // Called unconditionally: `useSources` is a plain extractor despite the name,
  // but a conditional call still reads as a hook to the linter.
  const citedSources = (useSources(message) as unknown[]) ?? [];
  const sources = showSources ? citedSources : [];
  const chart = normalizeKnowledgeChart(message);
  // The frame the chart node ran over — the whole result, not just the two
  // series columns the plot is drawn from.
  const dataframe = readChartDataframe(message);
  // The card draws the rows; the answer's own markdown table would repeat them.
  // Only a table that restates the frame is dropped — a pivot or a summary the
  // answer wrote over those rows is the answer, and stays. See
  // `stripResultTables`.
  const prose = dataframe?.rows.length
    ? stripResultTables(body, {
        columns: dataframe.columns,
        rows: dataframe.rows,
      })
    : body;
  const latency = get(message, ["debug_info", "latency_ms"]);
  // What the run did in the background. Read once the run has finished: a log
  // of a half-finished run would grow steps under the reader as it went.
  const runLog = streaming ? null : readRunLog(message);

  const trace = deriveTrace({
    loading: streaming,
    answer: body,
    sourceCount: sources.length,
    chartSuggestion: chart?.chartSuggestion,
    latencyMs: typeof latency === "number" ? latency : null,
    error,
  });

  const synthesize = useMemo(
    () => createSynthesizer({ baseUrl: ttsUrl, withCredentials: true }),
    [],
  );
  const tts = useTextToSpeech({
    synthesize,
    onError: (issue) => toast.error(ttsErrorMessage(issue.kind)),
  });
  const spoken = useMemo(() => toPlainText(body), [body]);

  return (
    <div className="flex flex-col gap-5">
      {question && (
        <div className="flex flex-col items-end gap-1">
          {/* The reader's mark sits beside their question the way the agent's
              sits beside its answer, so a long thread reads as two voices
              rather than as one column of bubbles. */}
          <div className="flex w-full items-start justify-end gap-3">
            <div className="max-w-[70%] rounded-[14px] rounded-br-[4px] bg-accent px-4 py-2.5 text-[13.5px] leading-relaxed text-accent-contrast">
              {question}
            </div>
            <span
              className="grid size-7 flex-none place-items-center rounded-[9px] bg-sunken text-ink-2"
              data-testid="asker-avatar"
            >
              <Icon name="user" className="size-4" />
            </span>
          </div>
          {questionViaVoice && (
            <span className="mr-10 text-[10px] text-ink-3">
              via voice input
            </span>
          )}
        </div>
      )}

      <div className="flex gap-3">
        <span className="grid size-7 flex-none place-items-center rounded-[9px] bg-accent-soft text-accent">
          <Icon name="spark" className="size-4" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-3.5">
          <DiRunTrace rows={trace} />

          {prose && (
            <div className="text-[13.5px] leading-relaxed text-ink-2">
              <Markdown>{prose}</Markdown>
            </div>
          )}

          {/* Nothing to explain until the run has finished, so Sources and Raw
              stay off the strip mid-stream rather than offering a fold over a
              half-written answer. */}
          <DiResultCard
            dataframe={dataframe}
            chart={chart}
            sources={streaming ? [] : (sources as DiSource[])}
            raw={message}
            rawEnabled={!streaming && rawEnabled}
          />

          {runLog && <DiRunLog log={runLog} />}

          {!streaming && body && (
            <div className="flex items-center gap-3 text-[11.5px]">
              <ChatFeedbackButtons
                sessionId={sessionId}
                chatId={String(get(message, "chat_id") ?? chatId)}
                userFeedback={
                  (get(message, "user_feedback") as string | null) ?? null
                }
                activeTab={activeTab}
                readOnly={readOnly}
                knowledgeId={knowledgeId}
              />
              <CopyToClipboard className="w-fit text-ink-3 hover:text-ink hover:no-underline">
                {body}
              </CopyToClipboard>
              {voiceOutputEnabled && ttsEnabled && (
                <ReadAloudButton
                  status={tts.status}
                  disabled={!spoken}
                  onPlay={() => tts.play(spoken)}
                  onPause={tts.pause}
                  onResume={tts.resume}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
