"use client";

import Icon from "@/components/Icon";

interface DiEmptyStateProps {
  /** The collection the questions will be answered from. */
  collectionName?: string;
  suggestions: string[];
  onPick: (question: string) => void;
}

export default function DiEmptyState({
  collectionName,
  suggestions,
  onPick,
}: DiEmptyStateProps) {
  return (
    <div className="flex flex-1 flex-col items-center overflow-y-auto px-6 py-8">
      <div className="my-auto flex w-full max-w-160 flex-col gap-5">
        <div className="flex flex-col gap-2">
          <div className="grid size-10.5 place-items-center rounded-di-lg bg-accent-soft text-accent">
            <Icon name="book" className="size-5" />
          </div>
          <h2 className="font-display text-[22px] font-bold text-ink">
            Ask a question about these documents.
          </h2>
          <p className="m-0 max-w-[52ch] text-[13.5px] leading-relaxed text-pretty text-ink-2">
            The agent reads
            {collectionName ? (
              <>
                {" "}
                <span className="font-mono text-[12.5px]">
                  {collectionName}
                </span>
              </>
            ) : (
              " your collection"
            )}
            , answers in plain English, and shows you the passages it drew from.
          </p>
        </div>

        {suggestions.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="text-[10.5px] font-bold tracking-[0.07em] text-ink-3 uppercase">
              Try one of these
            </div>
            <div className="flex flex-wrap gap-2">
              {suggestions.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => onPick(question)}
                  className="cursor-pointer rounded-full border border-line bg-surface px-3 py-2 text-[12.5px] font-medium text-ink-2 hover:border-accent hover:bg-accent-soft hover:text-accent-strong"
                >
                  {question}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
