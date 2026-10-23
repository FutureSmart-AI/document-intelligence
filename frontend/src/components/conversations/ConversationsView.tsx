"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyNote } from "@/components/ui";
import { useCollectionSettings } from "@/data/chatConfig";
import { useCollection } from "@/data/collections";
import {
  useConversations,
  useDeleteConversation,
  type ConversationRow,
} from "@/data/conversations";
import { knowledgeChatTransport } from "@/data/knowledgeChat";
import { useUserId } from "@/data/session";
import { useCollapsed } from "@/lib/useCollapsed";
import { useToast } from "@/platform/components/ToastContext";
import { useAuth } from "@/platform/hooks/useAuth";
import { useChatStream } from "@/platform/hooks/useChatStream";
import { useKnowledgeData } from "@/platform/hooks/useKnowledgeData";

import {
  CONVERSATION_TABS,
  isReadOnlyTab,
  type ConversationTab,
} from "./constants";
import DiChatHeader from "./DiChatHeader";
import DiComposer from "./DiComposer";
import DiDocumentsPanel from "./DiDocumentsPanel";
import DiEmptyState from "./DiEmptyState";
import DiSessionSidebar from "./DiSessionSidebar";
import DiThread from "./DiThread";

/**
 * Conversations with one collection's documents.
 *
 * The layout is the product's design artifact, and the Database Agent's screen's:
 * a sessions rail, a header naming what is being asked, the thread, the
 * composer, and the documents behind the answers on the right when the reader
 * asks for them.
 *
 * The chat itself is the agent UI's own stack — the streaming answer, the
 * sources beneath it, the follow-up questions, the feedback buttons — because
 * the artifact's promise that every answer carries the documents behind it is
 * that component's behaviour, not a rebuild of it. What is this product's own is
 * where the questions go: `POST /api/knowledge/v1/{knowledge_id}/chat`, with the
 * threads read back from the same service (see `data/knowledgeChat.ts`).
 */
export default function ConversationsView() {
  const { knowledgeId } = useAuth();
  const { toast } = useToast();
  const currentUserId = useUserId();
  const { collapsed: listCollapsed, toggle: toggleList } =
    useCollapsed("conversation-list");
  const [activeTab, setActiveTab] = useState<ConversationTab>("mine");
  const readOnly = isReadOnlyTab(activeTab);

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    null,
  );
  const [inputValue, setInputValue] = useState("");
  const [messageLoading, setMessageLoading] = useState(false);
  const previousSessionIdRef = useRef<string>("");
  // Closed to begin with: the thread is what the screen is for, and the
  // documents are the reference you reach for while writing a question.
  const [documentsOpen, setDocumentsOpen] = useState(false);
  // The rail's own state: the service does both the search and the paging.
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  // One transport per collection: `useChatStream` posts to its `url`, and that
  // url carries the knowledge id.
  const transport = useMemo(
    () => (knowledgeId ? knowledgeChatTransport(knowledgeId) : undefined),
    [knowledgeId],
  );

  const {
    sessionMessages,
    sessionId,
    // `messagesEndRef` / `messagesContainerRef` are deliberately not taken:
    // `DiThread` owns its own scroller and follows the thread itself.
    loading: chatLoading,
    chatId,
    error: chatError,
    fetchSessionDetails,
    callStreamAnswer,
    addMessage,
    resetChat,
  } = useChatStream(transport);

  // The collection's suggested questions, offered before the first question.
  const { data: settings } = useCollectionSettings(knowledgeId ?? undefined);
  const initialSuggestedQuestions = settings?.suggested_questions ?? [];
  const { data: collection } = useCollection(knowledgeId ?? undefined);

  // My Conversations is the signed-in account's own threads; a monitoring tab,
  // when the product offers one, leaves the route wide.
  const conversations = useConversations(knowledgeId, {
    page,
    search,
    userId: readOnly ? null : (currentUserId ?? null),
  });
  const removeConversation = useDeleteConversation(knowledgeId);
  const sessions = conversations.data?.sessions ?? [];

  /**
   * The documents behind the answers, for the panel and the rail's footer.
   *
   * The same list the Documents screen reads, so a document reads here exactly
   * as it does there — including the status that says whether an answer may
   * draw on it yet.
   */
  const documents = useKnowledgeData();
  const documentCount = documents.data?.allData.length;

  /**
   * What an answer may show, from the collection's own settings.
   *
   * A stored turn comes back carrying its evidence whether or not the
   * collection shows sources, so the gate has to be applied here rather than
   * trusted to the payload — the same rule the Database Agent's screen follows.
   */
  const showSources = settings?.show_sources !== false;
  const rawEnabled = Boolean(settings?.show_raw_response);

  // Selecting a conversation reads its thread; New chat empties it.
  useEffect(() => {
    if (!selectedSessionId) return;
    if (selectedSessionId === previousSessionIdRef.current) return;
    previousSessionIdRef.current = selectedSessionId;
    fetchSessionDetails(selectedSessionId).catch(() => {
      // Reported in the chat pane's error banner by the hook itself.
    });
    // `fetchSessionDetails` is re-created on every render of the hook; keying
    // the effect on it would re-read the thread continuously.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSessionId]);

  // The first question of a new chat opens the session, so the list has to be
  // re-read before that session can be shown as the selected one.
  useEffect(() => {
    if (!sessionId || selectedSessionId) return;
    if (sessionMessages.length === 0) return;
    previousSessionIdRef.current = sessionId;
    conversations.refetch().then(() => setSelectedSessionId(sessionId));
    // Keyed on the new session, not on the query object, which is re-created
    // on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, selectedSessionId, sessionMessages.length]);

  const handleNewChat = () => {
    // A new thread belongs to the signed-in account, so it can only be started
    // on that tab — starting one while watching everybody else's would open a
    // chat the list in front of you does not contain.
    setActiveTab("mine");
    setSelectedSessionId(null);
    previousSessionIdRef.current = "";
    resetChat();
    setInputValue("");
  };

  const handleTabChange = (tab: ConversationTab) => {
    setActiveTab(tab);
    setSelectedSessionId(null);
    previousSessionIdRef.current = "";
    setPage(1);
    resetChat();
  };

  const handleSelect = useCallback((nextSessionId: string) => {
    setSelectedSessionId(nextSessionId);
  }, []);

  const handleDelete = async (session: ConversationRow) => {
    try {
      const response = await removeConversation.mutateAsync(session.session_id);
      // The open thread has just been deleted: go back to a new chat rather
      // than leaving its answers on screen.
      if (session.session_id === selectedSessionId) handleNewChat();
      toast.success(response?.message || "Conversation deleted");
    } catch {
      // Reported by the shared axios interceptor.
    }
  };

  const handleSendMessage = useCallback(
    async (meta?: { viaVoice?: boolean }) => {
      if (!inputValue.trim()) return;
      try {
        addMessage(inputValue, meta?.viaVoice);
        setInputValue("");
        setMessageLoading(true);
        // The knowledge transport rewrites this into the body that route takes:
        // the question, the session when there is one, and nothing else — every
        // answer flag comes from the collection's stored chat config.
        await callStreamAnswer({
          query: inputValue,
          session_id: sessionId || "",
          stream: true,
        });
      } finally {
        setMessageLoading(false);
      }
    },
    [inputValue, sessionId, addMessage, callStreamAnswer],
  );

  if (!knowledgeId) {
    return (
      <div className="flex h-full flex-col bg-sunken">
        <div className="flex items-center justify-between border-b border-line bg-surface px-5 py-3.5">
          <b className="text-[15px] text-ink">Conversations</b>
        </div>
        <div className="p-5">
          <EmptyNote>
            This collection has no documents service, so it cannot answer
            questions.
          </EmptyNote>
        </div>
      </div>
    );
  }

  const showThread = Boolean(selectedSessionId) || sessionMessages.length > 0;
  /**
   * A stored conversation that came back with nothing in it.
   *
   * The question was asked and the run never produced an answer, so the thread
   * has no turn to draw — said plainly rather than left as an empty pane the
   * reader has to interpret.
   */
  const isUnanswered =
    Boolean(selectedSessionId) && !chatLoading && sessionMessages.length === 0;

  return (
    <div className="flex h-full min-h-0 bg-sunken">
      <DiSessionSidebar
        sessions={sessions}
        isLoading={conversations.isLoading}
        error={conversations.isError ? "Could not load conversations." : null}
        currentId={selectedSessionId}
        onSelect={handleSelect}
        onNewChat={handleNewChat}
        tabs={CONVERSATION_TABS.map((tab) => ({
          key: tab.key,
          label: tab.label,
          current: activeTab === tab.key,
          onSelect: () => handleTabChange(tab.key),
        }))}
        query={search}
        onQueryChange={(next) => {
          setSearch(next);
          setPage(1);
        }}
        onDelete={readOnly ? undefined : handleDelete}
        page={conversations.data?.current_page ?? page}
        totalPages={conversations.data?.total_pages ?? 1}
        onPageChange={setPage}
        showOwner={readOnly}
        currentUserId={currentUserId}
        collapsed={listCollapsed}
        onToggleCollapsed={toggleList}
        collection={
          collection
            ? {
                name: collection.name,
                documentCount,
                answering: collection.knowledgeEnabled,
              }
            : null
        }
      />

      {/* Not a <main>: the app shell already owns the page's main landmark,
          and nesting a second one leaves a screen reader with two. */}
      <div className="flex min-w-0 flex-1 flex-col">
        <DiChatHeader
          title={selectedSessionId ? "Conversation" : "New conversation"}
          collectionName={collection?.name}
          answering={collection ? collection.knowledgeEnabled : null}
          documentsOpen={documentsOpen}
          onToggleDocuments={() => setDocumentsOpen((open) => !open)}
        />

        {/* The thread and the documents sit side by side under the header, as
            in the artifact: the reference stays open while the thread
            scrolls. */}
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-bg">
            {isUnanswered && (
              <div className="flex-none border-b border-warn-dot/25 bg-warn-bg px-6 py-3">
                <p className="m-0 text-sm font-semibold text-warn-text">
                  This conversation has no saved answer.
                </p>
                <p className="m-0 mt-0.5 text-sm text-ink-2">
                  The question was asked, but the agent never produced an
                  answer, so there is nothing stored to show. Check this
                  collection&rsquo;s documents finished processing, then ask
                  again.
                </p>
              </div>
            )}

            {showThread ? (
              <DiThread
                messages={sessionMessages}
                loading={chatLoading}
                streaming={messageLoading}
                sessionId={selectedSessionId ?? sessionId}
                chatId={chatId}
                activeTab="newchat"
                readOnly={readOnly}
                knowledgeId={knowledgeId}
                showSources={showSources}
                rawEnabled={rawEnabled}
                voiceOutputEnabled={settings?.voice_output_enabled}
                initialSuggestedQuestions={initialSuggestedQuestions}
                onPickQuestion={setInputValue}
                error={chatError}
              />
            ) : (
              <DiEmptyState
                collectionName={collection?.name}
                suggestions={initialSuggestedQuestions}
                onPick={setInputValue}
              />
            )}

            {readOnly ? (
              <div className="flex-none border-t border-line bg-sunken px-4 py-3 text-center text-[11.5px] text-ink-3">
                Read-only conversation — switch to My Conversations to send
                messages.
              </div>
            ) : (
              <DiComposer
                value={inputValue}
                onChange={setInputValue}
                onSend={handleSendMessage}
                voiceInputEnabled={settings?.voice_input_enabled}
              />
            )}
          </div>

          {documentsOpen && (
            <DiDocumentsPanel
              collectionName={collection?.name}
              documents={documents.data?.allData ?? []}
              isLoading={documents.isLoading}
              error={
                documents.isError
                  ? "Could not read this collection's documents."
                  : null
              }
              onClose={() => setDocumentsOpen(false)}
            />
          )}
        </div>
      </div>
    </div>
  );
}
