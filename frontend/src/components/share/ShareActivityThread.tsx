"use client";

import { useEffect, useMemo } from "react";

import Icon from "@/components/Icon";
import { knowledgeChatTransport } from "@/data/knowledgeChat";
import ChatContainer from "@/platform/components/conversations/ChatContainer";
import { useChatStream } from "@/platform/hooks/useChatStream";

/**
 * Somebody else's conversation, opened read-only.
 *
 * The thread is rendered by the same chat stack the owner's own Conversations
 * screen uses — the answer, the documents underneath it, the follow-ups — so a
 * shared or bubble answer reads exactly as an owner answer does. That is the
 * point of opening it here at all: to see what this collection actually said.
 *
 * `readOnly` is what makes it a reading surface: the composer is replaced by
 * the shared note, and the feedback buttons do not act on an answer that was
 * given to somebody else.
 */
export default function ShareActivityThread({
  collectionId,
  sessionId,
  widget,
}: {
  collectionId: string;
  sessionId: string;
  /** A bubble visitor's thread, which the read-only note words differently. */
  widget?: boolean;
}) {
  const transport = useMemo(
    () => knowledgeChatTransport(collectionId),
    [collectionId],
  );

  const {
    sessionMessages,
    messagesEndRef,
    messagesContainerRef,
    loading,
    chatId,
    error,
    fetchSessionDetails,
  } = useChatStream(transport);

  useEffect(() => {
    if (!sessionId) return;
    fetchSessionDetails(sessionId).catch(() => {
      // Reported by the chat pane's own error banner.
    });
    // `fetchSessionDetails` is re-created on every render of the hook; keying
    // the effect on it would re-read the thread continuously.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  return (
    <ChatContainer
      sessionMessages={sessionMessages}
      loading={loading}
      inputValue=""
      setInputValue={() => {}}
      handleSendMessage={async () => {}}
      messagesEndRef={messagesEndRef}
      messagesContainerRef={messagesContainerRef}
      messageLoading={false}
      // Empty rather than a greeting: this is a thread being reopened, and a
      // greeting bubble would be put in a conversation that never had one.
      initialMessage=""
      readOnly
      readOnlyNote={
        <span className="inline-flex items-center gap-2 text-ink-2">
          <Icon name="lock" className="size-[15px] shrink-0" />
          {widget
            ? "Read-only — an anonymous visitor's conversation with the bubble."
            : "Read-only — this is someone else's conversation."}
        </span>
      }
      // What that stack calls its own view; only "widget" changes its copy.
      activeTab="newchat"
      sessionId={sessionId}
      chatId={chatId}
      error={error}
      knowledgeId={collectionId}
    />
  );
}
