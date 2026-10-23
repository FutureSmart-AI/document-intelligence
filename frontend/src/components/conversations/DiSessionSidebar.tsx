"use client";

import { useState } from "react";

import CollapseHandle from "@/components/CollapseHandle";
import Icon from "@/components/Icon";
import { Modal } from "@/components/ui";
import type { ConversationRow } from "@/data/conversations";

import { groupSessions } from "./groupSessions";

interface SidebarTab {
  key: string;
  label: string;
  current: boolean;
  onSelect: () => void;
}

interface DiSessionSidebarProps {
  sessions: ConversationRow[];
  isLoading: boolean;
  error?: string | null;
  currentId: string | null;
  onSelect: (sessionId: string) => void;
  onNewChat: () => void;
  /**
   * My Conversations, and the monitoring lists when the product offers them.
   *
   * Drawn only when there is more than one: a single tab is not a choice, and
   * the rail reads as the artifact's without it.
   */
  tabs: SidebarTab[];
  /** The search box's text. Narrowed by the service, so the view owns it. */
  query: string;
  onQueryChange: (query: string) => void;
  /**
   * Removes one conversation. Omitted on a list the reader may only watch —
   * a monitoring view is not a place to delete somebody else's thread from.
   */
  onDelete?: (session: ConversationRow) => void;
  /** Paging, because the sessions route hands back one page at a time. */
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  /**
   * The collection the questions are answered from, named in the footer, with
   * how many documents it holds and whether it is answering at all.
   */
  collection?: {
    name: string;
    documentCount?: number;
    answering: boolean;
  } | null;
  /** Who a session belongs to, on a list that shows more than one account. */
  showOwner?: boolean;
  currentUserId?: string;
  /**
   * Folds the rail down to a strip, remembered across visits.
   *
   * The list is dropped rather than hidden, so nothing under it can take a
   * click or a tab stop; the handle that brings it back rides the edge, as the
   * app shell's own sidebar handle does.
   */
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}

/** Who asked, as the service can name them: a person, an address, or an id. */
function owner(session: ConversationRow, currentUserId?: string): string {
  if (session.user_id && session.user_id === currentUserId) return "You";
  return (
    session.user_name ??
    session.user_email ??
    (session.user_id
      ? `User ${session.user_id.slice(0, 8)}`
      : "No signed-in user")
  );
}

export default function DiSessionSidebar({
  sessions,
  isLoading,
  error,
  currentId,
  onSelect,
  onNewChat,
  tabs,
  query,
  onQueryChange,
  onDelete,
  page,
  totalPages,
  onPageChange,
  collection,
  showOwner,
  currentUserId,
  collapsed = false,
  onToggleCollapsed,
}: DiSessionSidebarProps) {
  // The conversation the reader asked to delete, held until they confirm: a
  // thread and its answers do not come back.
  const [pendingDelete, setPendingDelete] = useState<ConversationRow | null>(
    null,
  );
  const groups = groupSessions(sessions);
  const pendingTitle = pendingDelete?.title || "Untitled conversation";

  if (collapsed) {
    return (
      <aside
        aria-label="Conversations"
        className="relative flex w-11 flex-none flex-col items-center border-r border-line bg-surface py-3"
      >
        <span className="text-[11.5px] tracking-wide text-ink-3 select-none [writing-mode:vertical-rl]">
          Conversations
        </span>
        {onToggleCollapsed && (
          <CollapseHandle
            collapsed
            onToggle={onToggleCollapsed}
            label="Expand conversations"
          />
        )}
      </aside>
    );
  }

  return (
    // `relative`, and never clipping: the collapse handle rides the right edge
    // and half of it hangs outside this panel.
    <aside
      aria-label="Conversations"
      className="relative flex w-[252px] flex-none flex-col border-r border-line bg-surface"
    >
      {onToggleCollapsed && (
        <CollapseHandle
          collapsed={false}
          onToggle={onToggleCollapsed}
          label="Collapse conversations"
        />
      )}
      <div className="flex flex-col gap-2.5 px-3.5 pt-3.5 pb-2.5">
        <button
          type="button"
          onClick={onNewChat}
          className="flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-di-md border-0 bg-accent px-3 py-2.5 text-[13px] font-semibold text-accent-contrast shadow-card hover:bg-accent-strong"
        >
          <Icon name="plus" className="size-4" />
          New chat
        </button>

        {tabs.length > 1 && (
          <div
            role="tablist"
            aria-label="Conversation source"
            className="flex gap-0.5 rounded-di-md bg-sunken p-0.5"
          >
            {tabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={tab.current}
                onClick={tab.onSelect}
                className={`flex-1 cursor-pointer truncate rounded-di-sm px-2 py-1 text-[11px] font-semibold ${
                  tab.current
                    ? "bg-surface text-ink shadow-card"
                    : "bg-transparent text-ink-3"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}

        <div className="flex items-center gap-1.5 rounded-di-md border border-line bg-sunken px-2.5 py-1.5">
          <Icon name="search" className="size-3 flex-none text-ink-3" />
          <input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search conversations…"
            aria-label="Search conversations"
            className="w-full border-0 bg-transparent text-xs text-ink outline-none placeholder:text-ink-3"
          />
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-3.5 overflow-y-auto px-2 pb-3">
        {error ? (
          <p className="px-2 text-xs text-danger-text">{error}</p>
        ) : isLoading ? (
          <div className="flex flex-col gap-1.5 px-2 pt-1">
            {[0, 1, 2].map((row) => (
              <div
                key={row}
                className="h-7 animate-pulse rounded-di-md bg-sunken"
              />
            ))}
          </div>
        ) : !groups.length ? (
          <p className="px-2 pt-1 text-xs text-ink-3">
            {query.trim()
              ? `No conversation matches “${query.trim()}”.`
              : "No conversations yet."}
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.label} className="flex flex-col gap-0.5">
              <div className="px-2 py-1 text-[10.5px] font-bold tracking-[0.07em] text-ink-3 uppercase">
                {group.label}
              </div>
              {group.items.map((session) => {
                const current = session.session_id === currentId;
                const title = session.title || "Untitled conversation";
                return (
                  <div
                    key={session.session_id}
                    className={`group flex items-center gap-1 rounded-di-md pr-1 ${
                      current ? "bg-accent-soft" : "hover:bg-sunken"
                    }`}
                  >
                    <button
                      type="button"
                      aria-current={current ? "true" : undefined}
                      onClick={() => onSelect(session.session_id)}
                      className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-di-md border-0 bg-transparent px-2.5 py-2 text-left"
                    >
                      <span
                        className={`size-1 flex-none rounded-full ${
                          current ? "bg-accent" : "bg-line-strong"
                        }`}
                      />
                      <span className="min-w-0 flex-1">
                        <span
                          className={`block truncate text-[12.5px] leading-snug ${
                            current
                              ? "font-semibold text-accent-strong"
                              : "font-medium text-ink-2"
                          }`}
                        >
                          {title}
                        </span>
                        {/* One line per conversation, as on the Database
                            Agent's rail. Who asked and how many questions is
                            worth a second line only on a list that holds more
                            than one account's threads — there the title alone
                            does not say whose it is. */}
                        {showOwner && (
                          <span className="block truncate text-[10.5px] text-ink-3">
                            {owner(session, currentUserId)} ·{" "}
                            {session.exchange_count}{" "}
                            {session.exchange_count === 1
                              ? "question"
                              : "questions"}
                          </span>
                        )}
                      </span>
                    </button>
                    {onDelete && (
                      <button
                        type="button"
                        aria-label={`Delete "${title}"`}
                        onClick={() => setPendingDelete(session)}
                        className="grid size-6 flex-none cursor-pointer place-items-center rounded-di-sm border-0 bg-transparent text-ink-3 opacity-0 group-hover:opacity-100 hover:text-danger-text focus-visible:opacity-100"
                      >
                        <Icon name="trash" className="size-3.5" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          ))
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center gap-2 border-t border-line px-3 py-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            className="cursor-pointer rounded-di-sm border border-line bg-surface px-2 py-1 text-[11px] font-semibold text-ink-2 hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-40"
          >
            Previous
          </button>
          <span className="flex-1 text-center text-[11px] text-ink-3">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
            className="cursor-pointer rounded-di-sm border border-line bg-surface px-2 py-1 text-[11px] font-semibold text-ink-2 hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}

      <Modal
        open={Boolean(pendingDelete)}
        title="Delete this conversation?"
        width="440px"
        onClose={() => setPendingDelete(null)}
        footer={
          <>
            <button
              type="button"
              onClick={() => setPendingDelete(null)}
              className="cursor-pointer rounded-di-md border border-line bg-surface px-3 py-2 text-[13px] font-semibold text-ink-2 hover:bg-sunken"
            >
              Keep it
            </button>
            <button
              type="button"
              onClick={() => {
                if (pendingDelete) onDelete?.(pendingDelete);
                setPendingDelete(null);
              }}
              className="cursor-pointer rounded-di-md border-0 bg-danger-dot px-3 py-2 text-[13px] font-semibold text-white hover:opacity-90"
            >
              Delete
            </button>
          </>
        }
      >
        <p className="m-0 text-[13px] text-ink-2">
          “{pendingTitle}” and every answer in it are removed. This cannot be
          undone — the thread leaves this list and the collection&rsquo;s
          history for everyone who can see it.
        </p>
      </Modal>

      {collection && (
        <div className="flex items-center gap-2 border-t border-line px-3.5 py-3">
          <span
            className={`size-[7px] flex-none rounded-full ${
              collection.answering
                ? "animate-pulse bg-success-dot"
                : "bg-warn-dot"
            }`}
          />
          <div className="min-w-0">
            <div className="truncate text-[11.5px] font-semibold text-ink">
              {collection.name}
            </div>
            <div className="text-[10.5px] text-ink-3">
              {collection.answering ? "Answering" : "Paused"}
              {collection.documentCount !== undefined &&
                ` · ${collection.documentCount} ${
                  collection.documentCount === 1 ? "document" : "documents"
                }`}
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
