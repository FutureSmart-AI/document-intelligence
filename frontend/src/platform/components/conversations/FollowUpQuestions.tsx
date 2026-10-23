"use client";

import type React from "react";

import { map } from "lodash-es";
import { Sparkles } from "lucide-react";

interface FollowUpQuestionsProps {
  questions: string[];
  onQuestionClick: (question: string) => void;
  messagesInputRef: React.RefObject<HTMLDivElement | null>;
}

const FollowUpQuestions = ({
  questions,
  onQuestionClick,
  messagesInputRef,
}: FollowUpQuestionsProps) => {
  if (!questions || questions.length === 0) {
    return null;
  }

  return (
    <div className="max-w-4xl mx-auto mt-6">
      {/* Header with icon */}
      <div className="flex items-center gap-2 mb-4 px-2">
        <div className="flex items-center justify-center w-6 h-6 rounded-full bg-linear-to-br from-indigo-500 to-purple-600 shadow-sm">
          <Sparkles className="w-3.5 h-3.5 text-white" />
        </div>
        <h3 className="text-sm font-semibold text-slate-700">
          Suggested Questions
        </h3>
      </div>

      {/* Questions grid */}
      <nav
        className="grid grid-cols-1 md:grid-cols-2 gap-3"
        aria-label="Suggested prompts"
      >
        {map(questions, (question, index) => (
          <button
            key={`${question}_${index}`}
            type="button"
            className="group cursor-pointer relative flex items-start gap-3 p-4 text-left bg-white border border-slate-200 rounded-xl hover:border-indigo-300 hover:shadow-md transition-all duration-200 hover:-translate-y-0.5"
            title={`Use suggested prompt: ${question}`}
            aria-label={`Use suggested prompt: ${question}`}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                onQuestionClick(question);
                messagesInputRef.current?.focus();
                e.preventDefault();
              }
            }}
            onClick={() => {
              onQuestionClick(question);
              messagesInputRef.current?.focus();
            }}
          >
            {/* Number badge */}
            <div className="shrink-0 flex items-center justify-center w-6 h-6 rounded-full bg-indigo-50 text-indigo-600 text-xs font-semibold group-hover:bg-indigo-100 transition-colors">
              {index + 1}
            </div>

            {/* Question text */}
            <div className="flex-1 min-w-0">
              <p className="text-sm text-slate-700 leading-relaxed wrap-break-word group-hover:text-indigo-700 transition-colors">
                {question}
              </p>
            </div>

            {/* Hover indicator */}
            <div className="absolute inset-0 rounded-xl bg-linear-to-br from-indigo-500/0 to-purple-500/0 group-hover:from-indigo-500/5 group-hover:to-purple-500/5 transition-all duration-200 pointer-events-none" />
          </button>
        ))}
      </nav>
    </div>
  );
};

export default FollowUpQuestions;
