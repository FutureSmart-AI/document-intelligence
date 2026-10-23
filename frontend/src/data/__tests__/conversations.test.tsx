import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import {
  conversationsFrom,
  useConversations,
  useDeleteConversation,
} from "../conversations";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const mockedGet = vi.mocked(axios.get);
const mockedDelete = vi.mocked(axios.delete);

const K = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";
const SESSION = "ac50bcab-b846-48b0-9088-f72b8af98781";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** One page of `GET /api/knowledge/v1/{knowledge_id}/sessions`. */
const page = {
  sessions: [
    {
      session_id: SESSION,
      title: "In one short sentence, what is this collection about?",
      exchange_count: 1,
      user_id: "c45844e8-b0f1-70d5-c97c-4ed460334d27",
      created_at: "2026-08-18T18:14:51.376203",
      updated_at: null,
    },
  ],
  total_count: 1,
  current_page: 1,
  total_pages: 1,
};

describe("useConversations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedGet.mockResolvedValue({ data: page });
  });

  it("reads the collection's own sessions, paged", async () => {
    const { result } = renderHook(() => useConversations(K), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(mockedGet).toHaveBeenCalledWith(`/api/knowledge/v1/${K}/sessions`, {
      params: { page: 1, page_size: 20 },
    });
    expect(result.current.data?.sessions[0].session_id).toBe(SESSION);
  });

  /** The service searches server-side; `q` is its own parameter. */
  it("passes a search through as q", async () => {
    renderHook(() => useConversations(K, { page: 2, search: "  invoices  " }), {
      wrapper,
    });
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());

    expect(mockedGet).toHaveBeenCalledWith(`/api/knowledge/v1/${K}/sessions`, {
      params: { page: 2, page_size: 20, q: "invoices" },
    });
  });

  /**
   * The date filter narrows on the service. Filtering the page in hand instead
   * would hide conversations that are only on the next one.
   */
  it("passes a date range through as the from instant", async () => {
    renderHook(() => useConversations(K, { range: "7" }), { wrapper });
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());

    const [, config] = mockedGet.mock.calls[0] as [
      string,
      { params: Record<string, unknown> },
    ];
    expect(typeof config.params.from).toBe("string");
    expect(Date.parse(config.params.from as string)).toBeGreaterThan(0);
  });

  it("sends no from when the filter is off", async () => {
    renderHook(() => useConversations(K, { range: "all" }), { wrapper });
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());

    const [, config] = mockedGet.mock.calls[0] as [
      string,
      { params: Record<string, unknown> },
    ];
    expect(config.params).not.toHaveProperty("from");
  });

  it("sends no q for an empty search box", async () => {
    renderHook(() => useConversations(K, { search: "   " }), { wrapper });
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());

    const [, config] = mockedGet.mock.calls[0] as [
      string,
      { params: Record<string, unknown> },
    ];
    expect(config.params).not.toHaveProperty("q");
  });

  /**
   * The two tabs are one query with and without this parameter, so a tab that
   * quietly stopped sending it would list every account's threads under "My
   * Conversations" and look right doing it.
   */
  it("narrows to one account when a user id is given", async () => {
    renderHook(() => useConversations(K, { userId: "e2e-user" }), { wrapper });
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());

    expect(mockedGet).toHaveBeenCalledWith(`/api/knowledge/v1/${K}/sessions`, {
      params: { page: 1, page_size: 20, user_id: "e2e-user" },
    });
  });

  it("sends no user_id when the list is everyone's", async () => {
    renderHook(() => useConversations(K, { userId: null }), { wrapper });
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());

    const [, config] = mockedGet.mock.calls[0] as [
      string,
      { params: Record<string, unknown> },
    ];
    expect(config.params).not.toHaveProperty("user_id");
  });

  it("asks nothing without a collection", () => {
    renderHook(() => useConversations(null), { wrapper });
    expect(mockedGet).not.toHaveBeenCalled();
  });
});

describe("useDeleteConversation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedDelete.mockResolvedValue({
      data: { session_id: SESSION, message: "Session deleted" },
    });
  });

  it("deletes one session on the knowledge service", async () => {
    const { result } = renderHook(() => useDeleteConversation(K), { wrapper });
    const response = await result.current.mutateAsync(SESSION);

    expect(mockedDelete).toHaveBeenCalledWith(
      `/api/knowledge/v1/${K}/sessions/${SESSION}`,
    );
    expect(response?.message).toBe("Session deleted");
  });
});

describe("the date filter's window", () => {
  const now = new Date("2026-08-21T15:30:00.000Z");

  it("starts today at the reader's own midnight", () => {
    const from = conversationsFrom("today", now);
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    expect(from).toBe(midnight.toISOString());
  });

  /** Seven days, not seven days rounded to a day boundary. */
  it("counts a rolling range back from now", () => {
    expect(conversationsFrom("7", now)).toBe(
      new Date(now.getTime() - 7 * 86_400_000).toISOString(),
    );
    expect(conversationsFrom("30", now)).toBe(
      new Date(now.getTime() - 30 * 86_400_000).toISOString(),
    );
  });

  it("asks for no window at all when the filter is off", () => {
    expect(conversationsFrom("all", now)).toBeUndefined();
  });
});
