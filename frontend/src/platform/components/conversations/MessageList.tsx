import type React from "react";
import { useEffect, useMemo, useRef } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";
import { get, isEmpty } from "lodash-es";

import ErrorMessage from "../ErrorMessage";
import { ChatMessage } from "./ChatMessage";
import FollowUpQuestions from "./FollowUpQuestions";

interface MessageListProps {
  sessionMessages: any[];
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
  voiceOutputEnabled?: boolean;
  initialMessage?: string;
  followUpQuestions: string[];
  showFollowUpQuestions: boolean;
  onQuestionClick: (question: string) => void;
  messagesInputRef: React.RefObject<HTMLDivElement | null>;
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
  messagesContainerRef?: React.RefObject<HTMLDivElement | null>;
  error?: string | null;
}

export const MessageList = ({
  sessionMessages,
  messageLoading,
  sessionId,
  activeTab,
  showSources,
  rawEnabled,
  rawView,
  readOnly,
  nl2sqlServiceId,
  knowledgeId,
  voiceOutputEnabled,
  initialMessage,
  followUpQuestions,
  showFollowUpQuestions,
  onQuestionClick,
  messagesInputRef,
  messagesEndRef,
  messagesContainerRef,
  error,
}: MessageListProps) => {
  const parentRef = useRef<HTMLDivElement>(null);

  // Memoize messages array to prevent unnecessary recalculations
  const messagesArray = useMemo(() => sessionMessages || [], [sessionMessages]);

  // Create virtualizer for messages
  const virtualizer = useVirtualizer({
    count: messagesArray.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 150, // Estimated height per message (will be measured dynamically)
    overscan: 5, // Render 5 extra items outside viewport for smoother scrolling
    measureElement:
      typeof window !== "undefined" &&
      navigator.userAgent.indexOf("Firefox") === -1
        ? (element) => element?.getBoundingClientRect().height
        : undefined, // Firefox has issues with measureElement
  });

  // Auto-scroll to bottom when new messages arrive (only if already near bottom)
  useEffect(() => {
    if (!parentRef.current || messagesArray.length === 0) return;

    const container = parentRef.current;
    const scrollHeight = container.scrollHeight;
    const scrollTop = container.scrollTop;
    const clientHeight = container.clientHeight;

    // Check if user is near the bottom (within 150px)
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 150;

    if (isNearBottom || messageLoading) {
      // Use virtualizer's scrollToIndex for better performance, or fallback to scrollIntoView
      requestAnimationFrame(() => {
        if (messagesArray.length > 0) {
          const lastIndex = messagesArray.length - 1;
          virtualizer.scrollToIndex(lastIndex, {
            align: "end",
            behavior: "auto",
          });
        } else if (messagesEndRef.current) {
          messagesEndRef.current.scrollIntoView({ behavior: "auto" });
        }
      });
    }
  }, [messagesArray.length, messageLoading, virtualizer, messagesEndRef]);

  const virtualItems = virtualizer.getVirtualItems();
  const hasMessages = messagesArray.length > 0;

  // Combined ref callback to set both refs
  const setRefs = (node: HTMLDivElement | null) => {
    parentRef.current = node;
    if (messagesContainerRef && node) {
      messagesContainerRef.current = node;
    }
  };

  return (
    <div
      ref={setRefs}
      className="flex-1 px-3 sm:px-6 py-4 overflow-x-hidden overflow-y-auto scroll-custom min-h-0"
      style={{ scrollbarGutter: "stable" }}
    >
      {/* Add ErrorMessage component at the top */}
      {error && <ErrorMessage message={error} />}

      <div className="relative" role="list" aria-live="polite">
        {/* Render the initial message if provided */}
        {initialMessage && (
          <div className="mb-6 p-4 bg-white border border-gray-100 shadow-sm rounded-xl w-fit">
            {initialMessage}
          </div>
        )}

        {/* Virtualized messages container */}
        {hasMessages ? (
          <div
            style={{
              height: `${virtualizer.getTotalSize()}px`,
              width: "100%",
              position: "relative",
            }}
          >
            {virtualItems.map((virtualItem) => {
              const item = messagesArray[virtualItem.index];
              if (!item) return null;

              const itemChatId = get(item, "chat_id");
              const role = get(item, "role");
              const messageKey = itemChatId
                ? `msg-${virtualItem.index}-${role}-${itemChatId}`
                : `msg-${virtualItem.index}-${role}-${get(item, "message", "").substring(0, 20)}`;

              return (
                <div
                  key={messageKey}
                  data-index={virtualItem.index}
                  ref={virtualizer.measureElement}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${virtualItem.start}px)`,
                  }}
                >
                  <div className="mb-6">
                    <ChatMessage
                      message={item}
                      precedingMessage={messagesArray[virtualItem.index - 1]}
                      index={virtualItem.index}
                      messageLoading={messageLoading}
                      sessionId={sessionId}
                      chatId={itemChatId}
                      activeTab={activeTab}
                      showSources={showSources}
                      rawEnabled={rawEnabled}
                      rawView={rawView}
                      readOnly={readOnly}
                      nl2sqlServiceId={nl2sqlServiceId}
                      knowledgeId={knowledgeId}
                      voiceOutputEnabled={voiceOutputEnabled}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          !initialMessage && (
            <div className="text-center text-slate-500 py-8">
              <p className="text-sm">No messages yet. Start a conversation!</p>
            </div>
          )
        )}

        {/* Show follow-up questions from messages in the messages area */}
        {showFollowUpQuestions &&
          !isEmpty(sessionMessages) &&
          !isEmpty(followUpQuestions) && (
            <div className="mt-6">
              <FollowUpQuestions
                questions={followUpQuestions}
                onQuestionClick={onQuestionClick}
                messagesInputRef={messagesInputRef}
              />
            </div>
          )}
      </div>

      {/* Reference to scroll to the bottom */}
      <div ref={messagesEndRef} />
    </div>
  );
};
