import type React from "react";

import { isEmpty } from "lodash-es";

import FollowUpQuestions from "./FollowUpQuestions";
import MessageBox from "./MessageBox";

interface ChatInputSectionProps {
  isChatEnabled: boolean;
  activeTab: string;
  inputValue: string;
  setInputValue: (value: string) => void;
  handleSendMessage: (meta?: { viaVoice?: boolean }) => void;
  messagesInputRef: React.RefObject<HTMLDivElement>;
  sessionMessages: any[];
  followUpQuestions: string[];
  showFollowUpQuestions: boolean;
  voiceInputEnabled?: boolean;
}

export const ChatInputSection = ({
  isChatEnabled,
  activeTab,
  inputValue,
  setInputValue,
  handleSendMessage,
  messagesInputRef,
  sessionMessages,
  followUpQuestions,
  showFollowUpQuestions,
  voiceInputEnabled,
}: ChatInputSectionProps) => {
  return (
    <>
      {/* Show initial suggested questions before input box when there are no messages */}
      {showFollowUpQuestions &&
        isEmpty(sessionMessages) &&
        !isEmpty(followUpQuestions) && (
          <div className="px-3 sm:px-6 pb-4 bg-gray-50">
            <FollowUpQuestions
              questions={followUpQuestions}
              onQuestionClick={setInputValue}
              messagesInputRef={messagesInputRef}
            />
          </div>
        )}

      {isChatEnabled &&
        (activeTab === "dashboard" || activeTab === "newchat") && (
          <div className="sticky bottom-0 p-4 bg-white/80 supports-backdrop-filter:backdrop-blur border-t border-slate-200">
            <MessageBox
              ref={messagesInputRef}
              inputValue={inputValue}
              setInputValue={setInputValue}
              getBotAnswer={handleSendMessage}
              voiceInputEnabled={voiceInputEnabled}
            />
          </div>
        )}
    </>
  );
};
