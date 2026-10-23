"use client";

interface DiFollowUpsProps {
  questions: string[];
  onPick: (question: string) => void;
}

/** The collection's suggested next questions, as dashed pills. */
export default function DiFollowUps({ questions, onPick }: DiFollowUpsProps) {
  if (!questions.length) return null;

  return (
    <nav
      aria-label="Suggested follow-up questions"
      className="flex flex-wrap gap-1.5 pt-1"
    >
      {questions.map((question) => (
        <button
          key={question}
          type="button"
          onClick={() => onPick(question)}
          className="cursor-pointer rounded-full border border-dashed border-line bg-transparent px-2.5 py-1.5 text-xs text-ink-2 hover:border-solid hover:border-accent hover:text-accent-strong"
        >
          {question}
        </button>
      ))}
    </nav>
  );
}
