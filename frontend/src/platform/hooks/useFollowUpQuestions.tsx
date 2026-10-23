import { useEffect, useRef, useState } from "react";

import { isEmpty, last } from "lodash-es";

import { extractFollowUpQuestions } from "@/platform/chat-core/utils";

interface UseFollowUpQuestionsProps {
  sessionMessages: any[];
  initialSuggestedQuestions?: string[];
}

export const useFollowUpQuestions = ({
  sessionMessages,
  initialSuggestedQuestions = [],
}: UseFollowUpQuestionsProps) => {
  const [followUpQuestions, setFollowUpQuestions] = useState<string[]>([]);

  // Seeded by content, not by identity. A caller that computes the starter list
  // inline hands over a new array on every render, and re-setting state from it
  // each time is a render loop — which takes focus away from whatever is being
  // typed. Comparing the questions themselves settles after the first pass.
  const initialKey = JSON.stringify(initialSuggestedQuestions);
  const initialRef = useRef(initialSuggestedQuestions);
  initialRef.current = initialSuggestedQuestions;

  useEffect(() => {
    if (!isEmpty(sessionMessages)) {
      const questions = extractFollowUpQuestions(last(sessionMessages));
      setFollowUpQuestions(questions.length > 0 ? questions : []);
    } else {
      setFollowUpQuestions(initialRef.current);
    }
  }, [sessionMessages, initialKey]);

  return followUpQuestions;
};
