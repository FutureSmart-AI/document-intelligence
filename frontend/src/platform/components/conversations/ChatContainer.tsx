"use client";

import type React from "react";
import { useRef } from "react";

import { useQuery } from "@tanstack/react-query";

import { useFollowUpQuestions } from "@/platform/hooks/useFollowUpQuestions";
import axios from "@/platform/utils/axios";

import { ChatInputSection } from "./ChatInputSection";
import { ChatLoadingState } from "./ChatLoadingState";
import { MessageList } from "./MessageList";

interface ChatContainerProps {
  sessionMessages: any[];
  loading: boolean;
  inputValue: string;
  setInputValue: (value: string) => void;
  handleSendMessage: (meta?: { viaVoice?: boolean }) => void;
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
  messagesContainerRef?: React.RefObject<HTMLDivElement | null>;
  messageLoading: boolean;
  initialMessage: string;
  activeTab: string;
  sessionId: string;
  chatId: string;
  error?: string | null;
  initialSuggestedQuestions?: string[];
  rawView?: boolean;
  readOnly?: boolean;
  /**
   * What the read-only strip says in place of the composer.
   *
   * The default names the tab a reader would switch to, which only exists in
   * the agent UI's own chat. A product that opens somebody else's thread from
   * a different screen passes its own wording instead.
   */
  readOnlyNote?: React.ReactNode;
  /**
   * The collection (knowledge base) the thread belongs to. Its chat config on
   * the knowledge service decides whether sources and raw responses are shown.
   */
  knowledgeId: string | null;
  /** Product-owned voice settings. */
  voiceInputEnabled?: boolean;
  voiceOutputEnabled?: boolean;
}
const EMPTY_QUESTIONS: string[] = [];

const ChatContainer = ({
  sessionMessages,
  loading,
  inputValue,
  setInputValue,
  handleSendMessage,
  activeTab,
  initialSuggestedQuestions = EMPTY_QUESTIONS,
  knowledgeId,
  voiceInputEnabled,
  voiceOutputEnabled,
  rawView,
  readOnly,
  readOnlyNote,
  ...rest
}: ChatContainerProps) => {
  const messagesInputRef = useRef<HTMLDivElement | null>(null);

  // The collection's own chat config, for the two things the payload cannot
  // say on its own: whether sources are shown, and whether each answer exposes
  // a "Raw response" button. The knowledge service keeps both flat on
  // `…/chat/config`; a config that has not arrived yet counts as "sources not
  // allowed", so the block can never flash up before the setting that hides it.
  const { data: chatParams, isPending: chatParamsPending } = useQuery({
    queryKey: ["knowledgeChatDisplay", knowledgeId],
    queryFn: async () => {
      const response = await axios.get(
        `/api/knowledge/v1/${knowledgeId}/chat/config`,
      );
      return {
        raw_response: response.data?.show_raw_response,
        debug_info: response.data?.show_sources,
      };
    },
    enabled: Boolean(knowledgeId),
    refetchOnWindowFocus: false,
  });
  const rawEnabled = Boolean(chatParams?.raw_response);
  const showSources = !chatParamsPending && chatParams?.debug_info !== false;
  const showFollowUpQuestions = true;

  // Custom hooks
  const followUpQuestions = useFollowUpQuestions({
    sessionMessages,
    initialSuggestedQuestions,
  });

  return (
    <div className="flex flex-col flex-1 h-full bg-gray-50">
      {loading ? (
        <ChatLoadingState />
      ) : (
        <MessageList
          sessionMessages={sessionMessages}
          activeTab={activeTab}
          showSources={showSources}
          rawEnabled={rawEnabled}
          rawView={rawView}
          readOnly={readOnly}
          knowledgeId={knowledgeId}
          voiceOutputEnabled={voiceOutputEnabled}
          followUpQuestions={followUpQuestions}
          showFollowUpQuestions={showFollowUpQuestions}
          onQuestionClick={setInputValue}
          messagesInputRef={messagesInputRef}
          {...rest}
        />
      )}

      {readOnly ? (
        <div className="border-t border-slate-200 bg-slate-50 px-4 py-3 text-center text-xs text-slate-500">
          {readOnlyNote ??
            (activeTab === "widget"
              ? "Anonymous widget session — read-only. Replies are not possible here."
              : "Read-only session — switch to My Conversations to send messages.")}
        </div>
      ) : (
        <ChatInputSection
          isChatEnabled
          activeTab={activeTab}
          inputValue={inputValue}
          setInputValue={setInputValue}
          handleSendMessage={handleSendMessage}
          messagesInputRef={messagesInputRef}
          sessionMessages={sessionMessages}
          followUpQuestions={followUpQuestions}
          showFollowUpQuestions={showFollowUpQuestions}
          voiceInputEnabled={voiceInputEnabled}
        />
      )}
    </div>
  );
};

export default ChatContainer;
