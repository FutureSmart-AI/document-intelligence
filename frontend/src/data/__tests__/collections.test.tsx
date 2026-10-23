import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import {
  useCollection,
  useCollections,
  useCollectionStats,
  useDeleteCollection,
} from "../collections";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("../session", () => ({
  useOwnerId: () => "owner@futuresmart.ai",
}));

const mockedGet = vi.mocked(axios.get);
const mockedDelete = vi.mocked(axios.delete);

const K1 = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";
const K2 = "3ca68247-87f5-4304-900c-33f8ecfa52e4";
const K3 = "2cacb101-2a85-4e4d-b407-2958559ccf85";

/** An error shaped like the ones axios throws, which `isAxiosError` recognises. */
const axiosError = (status: number, message = "request failed") =>
  Object.assign(new Error(message), {
    isAxiosError: true,
    response: { status },
  });

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** The shape `GET /api/knowledge/v1/{id}/config` answers. */
const config = (name: string | null, updated: string) => ({
  data: {
    knowledge_id: K1,
    collection_name: name,
    service_description: "resumes",
    embedding_model: "paraphrase-multilingual-mpnet-base-v2",
    hybrid_search_enabled: true,
    created_at: "2026-08-01T10:00:00+00:00",
    updated_at: updated,
  },
});

describe("useCollections", () => {
  beforeEach(() => vi.clearAllMocks());

  /**
   * The list route answers ids and a count, nothing else — measured against the
   * live service, where `include=config|all|metadata` changes the response not at
   * all. Every name on the board therefore costs one config read.
   */
  it("lists ids by owner, then reads each one's config", async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === "/api/knowledge/v1") {
        return {
          data: {
            owner_id: "owner@futuresmart.ai",
            knowledge_services: [K1, K2],
            count: 2,
          },
        };
      }
      if (url === `/api/knowledge/v1/${K1}/config`) {
        return config("Resumes", "2026-08-02T10:00:00+00:00");
      }
      return config("Invoices", "2026-08-03T10:00:00+00:00");
    });

    const { result } = renderHook(() => useCollections(), { wrapper });
    await waitFor(() => expect(result.current.collections).toHaveLength(2));

    expect(mockedGet).toHaveBeenCalledWith("/api/knowledge/v1", {
      params: { owner_id: "owner@futuresmart.ai" },
    });
    expect(mockedGet).toHaveBeenCalledWith(`/api/knowledge/v1/${K1}/config`);
    expect(mockedGet).toHaveBeenCalledWith(`/api/knowledge/v1/${K2}/config`);
    // Newest first, by the config's own timestamp.
    expect(result.current.collections.map((c) => c.name)).toEqual([
      "Invoices",
      "Resumes",
    ]);
    expect(result.current.collections[0]).toMatchObject({
      id: K2,
      knowledgeId: K2,
      knowledgeEnabled: true,
    });
  });

  /**
   * A knowledge base with no name is one created elsewhere in the platform —
   * every RAG agent has one. Showing those as "Untitled collection" would put
   * another product's documents on this board.
   */
  it("leaves unnamed knowledge bases off the board", async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === "/api/knowledge/v1") {
        return { data: { knowledge_services: [K1, K2], count: 2 } };
      }
      if (url === `/api/knowledge/v1/${K1}/config`) {
        return config("Resumes", "2026-08-02T10:00:00+00:00");
      }
      return config(null, "2026-08-03T10:00:00+00:00");
    });

    const { result } = renderHook(() => useCollections(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.collections.map((c) => c.id)).toEqual([K1]);
  });

  /**
   * A knowledge base created but never configured answers "Knowledge config
   * exists but is not initialized" on this route, and one mid-delete answers
   * 404. Neither may empty the board.
   */
  it("keeps the readable collections when one config fails", async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === "/api/knowledge/v1") {
        return { data: { knowledge_services: [K1, K3], count: 2 } };
      }
      if (url === `/api/knowledge/v1/${K3}/config`) {
        throw axiosError(400, "not initialized");
      }
      return config("Resumes", "2026-08-02T10:00:00+00:00");
    });

    const { result } = renderHook(() => useCollections(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.collections.map((c) => c.id)).toEqual([K1]);
  });

  /**
   * "Shared with me" is the whole point of the flag: the list route hands back
   * every collection this account can reach, and only validate says which of
   * them it owns.
   */
  it("flags the collections this account does not own as shared", async () => {
    mockedGet.mockImplementation(
      async (url: string, options?: { params?: { knowledge_id?: string } }) => {
        if (url === "/api/knowledge/v1") {
          return { data: { knowledge_services: [K1, K2], count: 2 } };
        }
        if (url === "/api/knowledge/v1/validate") {
          return {
            data: { valid: options?.params?.knowledge_id === K1 },
          };
        }
        if (url === `/api/knowledge/v1/${K1}/config`) {
          return config("Resumes", "2026-08-03T10:00:00+00:00");
        }
        return config("Invoices", "2026-08-02T10:00:00+00:00");
      },
    );

    const { result } = renderHook(() => useCollections(), { wrapper });
    await waitFor(() => expect(result.current.collections).toHaveLength(2));

    expect(mockedGet).toHaveBeenCalledWith("/api/knowledge/v1/validate", {
      params: { owner_id: "owner@futuresmart.ai", knowledge_id: K2 },
    });
    expect(
      Object.fromEntries(
        result.current.collections.map((c) => [c.id, c.shared]),
      ),
    ).toEqual({ [K1]: false, [K2]: true });
  });

  /**
   * A validate outage must not move every card to the other tab: the board has
   * always shown what it lists as this account's own, and that is the answer a
   * failed probe keeps.
   */
  it("treats a collection as owned when the ownership probe fails", async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === "/api/knowledge/v1") {
        return { data: { knowledge_services: [K1], count: 1 } };
      }
      if (url === "/api/knowledge/v1/validate") {
        throw axiosError(500, "validate is down");
      }
      return config("Resumes", "2026-08-03T10:00:00+00:00");
    });

    const { result } = renderHook(() => useCollections(), { wrapper });
    await waitFor(() => expect(result.current.collections).toHaveLength(1));
    expect(result.current.collections[0].shared).toBe(false);
  });
});

describe("useCollection", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads the collection's config and whether it answers questions", async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === `/api/knowledge/v1/${K1}/config`) {
        return config("Resumes", "2026-08-02T10:00:00+00:00");
      }
      if (url === `/api/knowledge/v1/${K1}/chat/config`) {
        return { data: { chat_enabled: false } };
      }
      throw new Error(`unexpected ${url}`);
    });

    const { result } = renderHook(() => useCollection(K1), { wrapper });
    await waitFor(() => expect(result.current.collection).not.toBeNull());

    expect(result.current.collection).toMatchObject({
      id: K1,
      knowledgeId: K1,
      name: "Resumes",
      about: "resumes",
      knowledgeEnabled: false,
    });
  });

  it("still resolves when the chat config cannot be read", async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === `/api/knowledge/v1/${K1}/config`) {
        return config("Resumes", "2026-08-02T10:00:00+00:00");
      }
      throw axiosError(403, "forbidden");
    });

    const { result } = renderHook(() => useCollection(K1), { wrapper });
    await waitFor(() => expect(result.current.collection).not.toBeNull());
    expect(result.current.collection?.knowledgeEnabled).toBe(true);
  });

  /**
   * An id this account cannot see cannot be fixed by retrying, and a query left
   * erroring holds no data — so every consumer that mounts asks again.
   */
  it("caches a null for a collection this account cannot see", async () => {
    mockedGet.mockRejectedValue(axiosError(404, "not found"));

    const { result } = renderHook(() => useCollection(K1), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.collection).toBeNull();
  });
});

describe("useCollectionStats", () => {
  beforeEach(() => vi.clearAllMocks());

  it("counts documents and fields from the v2 list and the enrichment fields", async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === `/api/knowledge/v2/${K1}/documents`) {
        return {
          data: {
            document: [
              { file_name: "a.pdf", status: "completed" },
              { file_name: "b.pdf", status: "processing" },
              { file_name: "c.pdf", status: "failed" },
            ],
          },
        };
      }
      return {
        data: {
          fields: [
            { field_name: "candidate_name", is_custom: true },
            { field_name: "document_type", is_custom: false },
          ],
        },
      };
    });

    const { result } = renderHook(() => useCollectionStats(K1, "Resumes"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(result.current.data).toMatchObject({
      documents: 3,
      processing: 1,
      failed: 1,
      fields: 2,
      customFields: 1,
    });
  });

  it("still answers when the fields cannot be read", async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === `/api/knowledge/v2/${K1}/documents`) {
        return {
          data: { document: [{ file_name: "a.pdf", status: "completed" }] },
        };
      }
      throw new Error("enrichment unavailable");
    });

    const { result } = renderHook(() => useCollectionStats(K1, "Resumes"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toMatchObject({ documents: 1, fields: 0 });
  });
});

describe("useDeleteCollection", () => {
  beforeEach(() => vi.clearAllMocks());

  it("deletes the collection through the knowledge service", async () => {
    mockedDelete.mockResolvedValue({ data: null });
    const target = {
      id: K1,
      knowledgeId: K1,
      name: "Resumes",
      knowledgeEnabled: true,
      about: null,
      shared: false,
      updatedAt: null,
      createdAt: null,
    };

    const { result } = renderHook(() => useDeleteCollection(), { wrapper });
    await result.current.mutateAsync(target);

    expect(mockedDelete).toHaveBeenCalledWith(`/api/knowledge/v1/${K1}`);
  });
});
