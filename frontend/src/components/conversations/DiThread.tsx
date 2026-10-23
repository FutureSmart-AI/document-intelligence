"use client";

import { useEffect, useRef } from "react";

import Icon from "@/components/Icon";
import { useFollowUpQuestions } from "@/platform/hooks/useFollowUpQuestions";

import DiFollowUps from "./DiFollowUps";
import DiTurnBlock from "./DiTurnBlock";
import { toTurns } from "./toTurns";

interface DiThreadProps {
  messages: unknown[];
  loading: boolean;
  streaming: boolean;
  sessionId: string;
  chatId: string;
  activeTab: string;
  readOnly?: boolean;
  knowledgeId?: string | null;
  showSources: boolean;
  rawEnabled: boolean;
  voiceOutputEnabled?: boolean;
  initialSuggestedQuestions?: string[];
  onPickQuestion: (question: string) => void;
  error?: string | null;
}

export default function DiThread({
  messages,
  loading,
  streaming,
  sessionId,
  chatId,
  activeTab,
  readOnly,
  knowledgeId,
  showSources,
  rawEnabled,
  voiceOutputEnabled,
  initialSuggestedQuestions,
  onPickQuestion,
  error,
}: DiThreadProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const turns = toTurns(messages);
  const followUps = useFollowUpQuestions({
    sessionMessages: messages,
    initialSuggestedQuestions,
  });

  // Follow the thread only when the reader is already at the bottom, so
  // scrolling back through an answer is not yanked away by the next chunk.
  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const distance =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    if (distance < 160) element.scrollTop = element.scrollHeight;
  }, [messages, streaming]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-ink-3">
        <Icon name="ref" className="size-4 animate-spin" />
        Loading this conversation…
      </div>
    );
  }

  return (
    <div
      ref={scroller}
      className="min-h-0 flex-1 overflow-y-auto px-6 pt-6 pb-2"
    >
      <div className="mx-auto flex max-w-205 flex-col gap-5">
        {error && (
          <div className="rounded-di-lg border border-danger-dot/25 bg-danger-bg px-4 py-3 text-[12.5px] text-danger-text">
            {error}
          </div>
        )}

        {!turns.length && !error && (
          <p className="py-8 text-center text-sm text-ink-3">
            No messages in this conversation yet.
          </p>
        )}

        {turns.map((turn, index) => {
          const last = index === turns.length - 1;
          return (
            <DiTurnBlock
              key={turn.key}
              message={turn.answer ?? { role: "assistant", response: "" }}
              question={turn.question}
              questionViaVoice={turn.questionViaVoice}
              sessionId={sessionId}
              chatId={chatId}
              activeTab={activeTab}
              readOnly={readOnly}
              knowledgeId={knowledgeId}
              showSources={showSources}
              rawEnabled={rawEnabled}
              voiceOutputEnabled={voiceOutputEnabled}
              streaming={turn.answer ? last && streaming : true}
              error={last ? error : null}
            />
          );
        })}

        {!streaming && turns.length > 0 && (
          <DiFollowUps questions={followUps} onPick={onPickQuestion} />
        )}
      </div>
    </div>
  );
}
