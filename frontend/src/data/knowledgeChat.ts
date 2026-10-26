"use client";

import type { DiRunBlock } from "@/components/conversations/runLog";
import type { ChatStreamTransport } from "@/platform/hooks/useChatStream";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * Asking a collection a question, and reading a stored thread back.
 *
 * This product does not route questions through the chat service: a question
 * goes to `POST /api/knowledge/v1/{knowledge_id}/chat` and the thread is read
 * from that service's own session routes. The stream itself is nearly the one the
 * shared chat stack already reads (measured against the live service):
 *
 *     data: {"chunk": "It covers …"}
 *     data: {"query": …, "response": …, "session_id": …, "chat_id": …,
 *            "data": {"knowledge": {"relevant_sources": …, "summaries": …}}}
 *
 * so the answer, the follow-up questions and the feedback ids all land where
 * `useChatStream` expects them. Three things differ, and they are all this module
 * is: the request body, the shape of a **stored** thread, and what the evidence
 * under an answer is called.
 */

/**
 * `data.knowledge` as this service sends it.
 *
 * The chat stack reads `data.knowledge.sources` (or `chunks` + `summaries`).
 * This service names the same evidence differently — `relevant_sources.data`,
 * `summaries` keyed by document id, `detailed_chunks` — so without the mapping
 * below every answer rendered with no sources under it at all, which is the one
 * thing this product promises.
 */
export interface KnowledgeEvidence {
  sources?: unknown[];
  chunks?: unknown[];
  detailed_chunks?: unknown[];
  relevant_sources?: { data?: Array<Record<string, unknown>> };
  summaries?: Record<
    string,
    { summary?: string; filename?: string; metadata?: unknown }
  >;
  [key: string]: unknown;
}

/**
 * Rewrites one `data` payload into the shape the chat stack reads.
 *
 * Chunks are preferred when the answer carried them: `processChunks` groups them
 * per document and folds in the summaries, which is how a source card gets its
 * sections. Otherwise the retrieved documents become one source each, with the
 * summary text and the document metadata this service keeps beside them.
 */
export function adaptKnowledgeData(
  data: Record<string, unknown> | null | undefined,
): Record<string, unknown> | null | undefined {
  const knowledge = data?.knowledge as KnowledgeEvidence | undefined;
  if (!knowledge) return data;
  // Already in the read shape (or empty): nothing to do.
  if (knowledge.sources || knowledge.chunks) return data;

  const summaries = knowledge.summaries ?? {};
  const chunks = knowledge.detailed_chunks;
  if (Array.isArray(chunks) && chunks.length > 0) {
    return {
      ...data,
      knowledge: { ...knowledge, chunks, summaries },
    };
  }

  const retrieved = knowledge.relevant_sources?.data;
  if (!Array.isArray(retrieved) || retrieved.length === 0) return data;

  const sources = retrieved.map((source) => {
    const id = String(source.id ?? "");
    const summary = summaries[id];
    return {
      ...source,
      document_id: id || undefined,
      file_name: summary?.filename ?? source.title ?? "Unknown",
      context: summary?.summary ?? source.context ?? "",
      summary: summary?.summary,
      metadata: summary?.metadata,
    };
  });

  return { ...data, knowledge: { ...knowledge, sources, summaries } };
}

/** One message of `GET /api/knowledge/v1/{knowledge_id}/sessions/{session_id}`. */
export interface KnowledgeWireMessage {
  chat_id?: string;
  role?: "user" | "assistant";
  message?: string;
  created_at?: string | null;
  user_feedback?: string | null;
  /** Already an object here; the dashboard service stored this as a JSON string. */
  data?: Record<string, unknown> | null;
  /** The question as the service decided to ask it. */
  refined_query?: string | null;
  llm_provider?: string | null;
  model?: string | null;
  is_answered?: boolean | string | null;
  /** The turn's usage block, stored with it; null for turns stored before that. */
  usage?: Record<string, unknown> | null;
}

/**
 * The stored thread in the shape `useChatStream` maps.
 *
 * Its mapper reads `data` for the sources block and falls back to `debug_info`
 * only when `data.knowledge` is empty — which is exactly how these rows arrive,
 * so the messages pass through unchanged apart from being named
 * `conversation_data`.
 */
export function toConversationData(messages: KnowledgeWireMessage[]) {
  return messages.map((message) => {
    const data = adaptKnowledgeData(message.data) ?? null;
    const role = message.role ?? "assistant";
    // Only an answer has a run behind it — a question carries no log, and
    // attaching an empty one to it would put a "Full logs" fold under the
    // reader's own words.
    const run = role === "assistant" ? storedRunBlock(message) : undefined;
    return {
      chat_id: message.chat_id ?? "",
      role,
      message: message.message ?? "",
      data: data && run ? { ...data, di_run: run } : data,
      user_feedback: message.user_feedback ?? null,
    };
  });
}

/**
 * The run report a stored turn can still supply.
 *
 * The service stores the turn's `usage` block with it, so a reloaded turn shows
 * the same timings, models and cost as it did live. `debug_info` is still
 * live-only, and turns stored before `usage` was kept have none — `stored` plus
 * a missing `usage` is what tells the panel to say so instead of printing zero.
 */
function storedRunBlock(message: KnowledgeWireMessage): DiRunBlock | undefined {
  const block: DiRunBlock = {
    ...(message.usage && typeof message.usage === "object"
      ? { usage: message.usage }
      : {}),
    ...(message.refined_query ? { refined_query: message.refined_query } : {}),
    ...(message.llm_provider ? { llm_provider: message.llm_provider } : {}),
    ...(message.model ? { model: message.model } : {}),
    ...(message.is_answered !== null && message.is_answered !== undefined
      ? { is_answered: message.is_answered }
      : {}),
  };
  // A turn the service told us nothing about gets no block at all, rather than
  // a log whose every step is missing.
  if (!Object.keys(block).length) return undefined;
  return { ...block, stored: true };
}

/**
 * `POST …/chat` takes a different body from the chat service: no `agent_id`
 * (the knowledge id is in the path), no chunk count, and no per-turn answer
 * flags.
 *
 * The flags are deliberately omitted rather than sent: `show_sources`,
 * `follow_up` and `include_charts` are resolved from the collection's stored
 * chat config, so sending them from here would let a stale copy of Settings
 * override the setting a person just saved.
 */
export function toChatRequest(payload: Record<string, unknown>) {
  return {
    query: payload.query,
    stream: true,
    // A first question opens the session, so an empty id must be omitted
    // entirely rather than sent as "".
    ...(payload.session_id ? { session_id: payload.session_id } : {}),
  };
}

/**
 * The fields of the final stream event that describe the run rather than the
 * answer. Returns undefined for a `chunk` event, which carries none of them.
 */
function liveRunBlock(event: Record<string, unknown>): DiRunBlock | undefined {
  const keys = [
    "query",
    "refined_query",
    "is_answered",
    "usage",
    "debug_info",
    "byok_warnings",
  ] as const;
  const present = keys.filter((key) => event[key] !== undefined);
  if (!present.length) return undefined;
  return Object.fromEntries(
    present.map((key) => [key, event[key]]),
  ) as DiRunBlock;
}

/**
 * The transport for one collection. `useChatStream` prefixes `url` with the API
 * base, so this stays a path.
 */
export function knowledgeChatTransport(
  knowledgeId: string,
): ChatStreamTransport {
  return {
    url: buildApiUrl(APIS.CHAT, { knowledge_id: knowledgeId }),
    buildBody: toChatRequest,
    adaptEvent: (event) => {
      // `useChatStream` rebuilds the finished message from a fixed set of
      // fields and drops the rest of the final event — but it merges and keeps
      // `data`. The run's own report therefore travels inside `data`, which
      // keeps Full logs a Document Intelligence concern rather than a change to
      // a hook the dashboard and the Database Agent also stream through.
      const run = liveRunBlock(event as Record<string, unknown>);
      const data = event.data
        ? adaptKnowledgeData(event.data as Record<string, unknown>)
        : undefined;
      if (!data && !run) return event;
      return {
        ...event,
        data: { ...(data ?? {}), ...(run ? { di_run: run } : {}) },
      };
    },
    fetchSession: async (sessionId: string) => {
      const response = await axios.get<{ messages?: KnowledgeWireMessage[] }>(
        buildApiUrl(APIS.SESSION, {
          knowledge_id: knowledgeId,
          session_id: sessionId,
        }),
      );
      return {
        conversation_data: toConversationData(response.data?.messages ?? []),
      };
    },
  };
}
