import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import {
  adaptKnowledgeData,
  knowledgeChatTransport,
  toChatRequest,
  toConversationData,
} from "../knowledgeChat";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const mockedGet = vi.mocked(axios.get);

const K = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";
const SESSION = "ac50bcab-b846-48b0-9088-f72b8af98781";
const CHAT = "3b83a158-65cf-4bbc-8b8a-de64581c2a74";

describe("knowledgeChatTransport", () => {
  it("posts questions to the collection's own chat route", () => {
    expect(knowledgeChatTransport(K).url).toBe(`/api/knowledge/v1/${K}/chat`);
  });

  /** Not the chat service's `chat_completion`, which is what it used to be. */
  it("never points at the chat service", () => {
    expect(knowledgeChatTransport(K).url).not.toContain("/api/chat/");
  });
});

/**
 * The shared chat components build the chat service's payload. This is where it
 * becomes the body this route takes.
 */
describe("toChatRequest", () => {
  const payload = {
    agent_id: K,
    session_id: "",
    query: "who has Python experience?",
    no_of_chunks: 3,
    use_user_info: false,
    stream: true,
    chart_output: false,
  };

  it("sends the question and asks for a stream", () => {
    expect(toChatRequest(payload)).toEqual({
      query: "who has Python experience?",
      stream: true,
    });
  });

  it("carries the session id once one exists", () => {
    expect(toChatRequest({ ...payload, session_id: SESSION })).toEqual({
      query: "who has Python experience?",
      stream: true,
      session_id: SESSION,
    });
  });

  /** An empty id must be omitted, not sent as "" — that opens no session. */
  it("omits an empty session id entirely", () => {
    expect(toChatRequest(payload)).not.toHaveProperty("session_id");
  });

  /**
   * Every answer flag is resolved from the collection's stored chat config. An
   * explicit value in the body would override it, which is how the NL2SQL
   * product's Settings screen once ended up inert.
   */
  it("never overrides the collection's saved answer settings", () => {
    const sent = toChatRequest(payload) as Record<string, unknown>;
    for (const field of [
      "agent_id",
      "no_of_chunks",
      "chart_output",
      "debug_info",
      "follow_up",
      "show_sources",
    ]) {
      expect(sent).not.toHaveProperty(field);
    }
  });
});

/**
 * The evidence under an answer.
 *
 * The chat stack reads `data.knowledge.sources`, or `chunks` plus `summaries`.
 * This service sends `relevant_sources.data`, `summaries` keyed by document id
 * and `detailed_chunks` — so without this mapping every answer rendered with no
 * sources at all, which is the one thing this product promises.
 */
describe("adaptKnowledgeData", () => {
  const summaries = {
    "doc-1": {
      summary: "Six years of Python, three of them on data pipelines.",
      filename: "priya-sharma-resume.pdf",
      metadata: { document_type: "Resume" },
    },
  };

  it("turns the retrieved documents into sources the UI reads", () => {
    const adapted = adaptKnowledgeData({
      knowledge: {
        relevant_sources: {
          data: [{ id: "doc-1", title: "priya.pdf", context: "a snippet" }],
          tool_name: "rag",
        },
        summaries,
      },
    }) as { knowledge: { sources: Array<Record<string, unknown>> } };

    expect(adapted.knowledge.sources).toEqual([
      {
        id: "doc-1",
        title: "priya.pdf",
        document_id: "doc-1",
        // The summary's own file name and text win: they are the document's,
        // where the retrieved entry's are the matching passage's.
        file_name: "priya-sharma-resume.pdf",
        context: "Six years of Python, three of them on data pipelines.",
        summary: "Six years of Python, three of them on data pipelines.",
        metadata: { document_type: "Resume" },
      },
    ]);
  });

  it("falls back to the retrieved entry when there is no summary for it", () => {
    const adapted = adaptKnowledgeData({
      knowledge: {
        relevant_sources: {
          data: [{ id: "doc-9", title: "x.pdf", context: "c" }],
        },
      },
    }) as { knowledge: { sources: Array<Record<string, unknown>> } };

    expect(adapted.knowledge.sources[0]).toMatchObject({
      file_name: "x.pdf",
      context: "c",
    });
  });

  /** Chunks group per document and carry their sections, so they win. */
  it("prefers the detailed chunks when the answer carried them", () => {
    const adapted = adaptKnowledgeData({
      knowledge: {
        detailed_chunks: [
          {
            document_id: "doc-1",
            file_name: "priya-sharma-resume.pdf",
            text: "…",
          },
        ],
        relevant_sources: { data: [{ id: "doc-1", title: "priya.pdf" }] },
        summaries,
      },
    }) as { knowledge: Record<string, unknown> };

    expect(adapted.knowledge.chunks).toHaveLength(1);
    expect(adapted.knowledge).not.toHaveProperty("sources");
    expect(adapted.knowledge.summaries).toEqual(summaries);
  });

  it("leaves a payload that is already in the read shape alone", () => {
    const data = { knowledge: { sources: [{ file_name: "a.pdf" }] } };
    expect(adaptKnowledgeData(data)).toBe(data);
  });

  it("leaves a turn with no evidence alone", () => {
    expect(adaptKnowledgeData({})).toEqual({});
    expect(adaptKnowledgeData(null)).toBeNull();
    expect(
      adaptKnowledgeData({ knowledge: { knowledge_evidence: [] } }),
    ).toEqual({ knowledge: { knowledge_evidence: [] } });
  });
});

/**
 * A stored thread. The knowledge service returns `data` as an object — the
 * dashboard service stored the same thing as a JSON string — so the rows pass
 * through with only their evidence renamed.
 */
describe("toConversationData", () => {
  const messages = [
    {
      n: 1,
      chat_id: CHAT,
      role: "user" as const,
      message: "what is this collection about?",
      created_at: "2026-08-18T18:14:51.394443",
      user_feedback: null,
      data: {},
    },
    {
      n: 1,
      chat_id: CHAT,
      role: "assistant" as const,
      message: "It covers two documents.",
      created_at: "2026-08-18T18:14:51.394443",
      user_feedback: "positive",
      data: {
        knowledge: {
          relevant_sources: {
            data: [{ id: "doc-1", title: "voice-to-blog.md" }],
          },
        },
      },
    },
  ];

  it("keeps the fields the chat stack reads", () => {
    const [question, answer] = toConversationData(messages);

    expect(question).toEqual({
      chat_id: CHAT,
      role: "user",
      message: "what is this collection about?",
      data: {},
      user_feedback: null,
    });
    expect(answer).toMatchObject({
      chat_id: CHAT,
      role: "assistant",
      message: "It covers two documents.",
      user_feedback: "positive",
    });
    // The evidence is renamed on the way through, so a reloaded thread shows the
    // same sources the live answer did.
    expect(
      (answer.data as { knowledge: { sources: unknown[] } }).knowledge.sources,
    ).toHaveLength(1);
  });

  it("survives a thread with no messages", () => {
    expect(toConversationData([])).toEqual([]);
  });
});

describe("the transport's session read", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads the thread from the knowledge service and names it conversation_data", async () => {
    mockedGet.mockResolvedValue({
      data: {
        session_id: SESSION,
        knowledge_id: K,
        exchange_count: 1,
        messages: [
          { chat_id: CHAT, role: "assistant", message: "hi", data: null },
        ],
      },
    });

    const transport = knowledgeChatTransport(K);
    const thread = await transport.fetchSession!(SESSION);

    expect(mockedGet).toHaveBeenCalledWith(
      `/api/knowledge/v1/${K}/sessions/${SESSION}`,
    );
    expect(thread).toEqual({
      conversation_data: [
        {
          chat_id: CHAT,
          role: "assistant",
          message: "hi",
          data: null,
          user_feedback: null,
        },
      ],
    });
  });
});
