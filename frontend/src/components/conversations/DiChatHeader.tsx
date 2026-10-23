"use client";

import Icon from "@/components/Icon";

interface DiChatHeaderProps {
  title: string;
  /** The collection the questions are answered from. */
  collectionName?: string;
  /** Null while the collection is still loading. */
  answering: boolean | null;
  /** Whether the documents panel is open, when there are documents to show. */
  documentsOpen?: boolean;
  onToggleDocuments?: () => void;
}

/** Title, collection line and answering state above the thread. */
export default function DiChatHeader({
  title,
  collectionName,
  answering,
  documentsOpen,
  onToggleDocuments,
}: DiChatHeaderProps) {
  return (
    <header className="flex flex-none items-center gap-3 border-b border-line bg-surface px-5 py-3">
      <div className="min-w-0">
        <h1 className="truncate font-display text-[15px] font-bold text-ink">
          {title}
        </h1>
        {collectionName && (
          <div className="mt-px text-[11.5px] text-ink-3">
            Asking {collectionName}
          </div>
        )}
      </div>
      <div className="flex-1" />
      {answering !== null && (
        <span
          title={
            answering
              ? "This collection is answering questions."
              : "Answering is switched off for this collection under Settings."
          }
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
            answering
              ? "bg-success-bg text-success-text"
              : "bg-warn-bg text-warn-text"
          }`}
        >
          <Icon name={answering ? "chk" : "warn"} className="size-3" />
          {answering ? "Answering" : "Paused"}
        </span>
      )}
      {onToggleDocuments && (
        <button
          type="button"
          onClick={onToggleDocuments}
          aria-pressed={Boolean(documentsOpen)}
          className={`inline-flex cursor-pointer items-center gap-1.5 rounded-di-md border px-2.5 py-1.5 text-xs font-semibold ${
            documentsOpen
              ? "border-accent bg-accent-soft text-accent-strong"
              : "border-line bg-surface text-ink-2 hover:bg-sunken hover:text-ink"
          }`}
        >
          <Icon name="book" className="size-3" />
          Documents
        </button>
      )}
    </header>
  );
}
