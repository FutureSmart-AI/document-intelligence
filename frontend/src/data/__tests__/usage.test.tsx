import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import {
  keyTypeFor,
  labelChoices,
  operationChoices,
  operationLabeller,
  percentChange,
  periodRanges,
  queryFilters,
  quotaRows,
  useAnalytics,
  useLimitsScreen,
  useServiceOptions,
  useUsageStatus,
  type ServiceOptions,
} from "../usage";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("../session", () => ({
  useOwnerId: () => "owner@futuresmart.ai",
}));

const mockedGet = vi.mocked(axios.get);
const mockedPost = vi.mocked(axios.post);

// `buildApiUrl` substitutes the id verbatim, and the service matches the raw
// `@` in the path — an encoded one is not what the app actually sends.
const OWNER = "owner@futuresmart.ai";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/**
 * The scope is the thing worth pinning.
 *
 * This surface moved from `/v1/{knowledge_id}/usage/…` to
 * `/v1/owners/{owner_id}/usage/…` on the service, and the app went on calling
 * the old paths — which 404 — because nothing asserted which one it used. Each
 * test below reads the URL actually sent.
 */
describe("usage reads the account, not a collection", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads limits once, against the owner", async () => {
    mockedGet.mockResolvedValue({
      data: {
        owner_id: "owner@futuresmart.ai",
        period: { start: "2026-08-01T00:00:00+00:00", end: "2026-09-01" },
        limits: {
          max_agents: null,
          max_indexed_characters: 1_000_000,
          max_conversations_per_month: null,
          max_messages_per_month: null,
          max_llm_tokens_per_month: 500_000,
        },
        current_usage: {
          indexed_characters: 300,
          conversations_this_month: 3,
          messages_this_month: 12,
          llm_tokens_this_month: 3_000,
        },
        remaining: {
          indexed_characters: 999_700,
          conversations: null,
          messages: null,
          llm_tokens: 497_000,
        },
      },
    });

    const { result } = renderHook(() => useUsageStatus(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // One request for the whole account — not one per collection, summed.
    expect(mockedGet).toHaveBeenCalledTimes(1);
    expect(mockedGet.mock.calls[0][0]).toBe(
      `/api/knowledge/v1/owners/${OWNER}/usage/limits/status`,
    );
    expect(result.current.data?.current_usage.llm_tokens_this_month).toBe(3000);
    // A null limit is unlimited, and stays null rather than becoming zero.
    expect(result.current.data?.limits.max_conversations_per_month).toBeNull();
  });

  /**
   * The Limits tab reads this one instead of `limits/status`: the same
   * allowances, but with `percent`, `lifted` and `enforced` already decided by
   * the service that enforces them, so nothing on the tab is re-derived here.
   */
  it("reads the pre-computed limits screen, against the owner", async () => {
    mockedGet.mockResolvedValue({
      data: {
        owner_id: OWNER,
        tier: { name: "Trial", tier_type: "trial", is_default: true },
        window: { start: "2026-08-01T00:00:00+00:00", end: "2026-09-01" },
        own_credentials: ["groq"],
        exceeded: [],
        groups: [
          {
            group: "Workspace",
            items: [
              {
                key: "max_documents",
                label: "Documents you can hold",
                used: 12,
                limit: null,
                unit: "documents",
                maximum: "No limit on this account",
                note: "A sub-cap of the combined workspace item limit.",
                window: "current",
                lifted: false,
                percent: null,
                enforced: true,
              },
            ],
          },
        ],
      },
    });

    const { result } = renderHook(() => useLimitsScreen(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockedGet).toHaveBeenCalledTimes(1);
    expect(mockedGet.mock.calls[0][0]).toBe(
      `/api/knowledge/v1/owners/${OWNER}/usage/limits/screen`,
    );
    // A cap awaiting its backfill arrives as null, and stays null rather than
    // becoming a zero ceiling the reader is already over.
    expect(result.current.data?.groups[0].items[0].limit).toBeNull();
    expect(result.current.data?.groups[0].items[0].percent).toBeNull();
  });

  it("posts the analytics query to the owner's route", async () => {
    mockedPost.mockResolvedValue({
      data: {
        owner_id: "owner@futuresmart.ai",
        executed_at: "2026-08-20T00:00:00Z",
        date_range: { start: "2026-07-21", end: "2026-08-20" },
        group_by: ["knowledge_id"],
        totals: { request_count: 8, cost: { total_cost_usd: 1.25 } },
        data: [],
        total_groups: 0,
        limit: 1000,
        offset: 0,
        has_more: false,
      },
    });

    const query = {
      date_range: { start: "2026-07-21", end: "2026-08-20" },
      group_by: ["knowledge_id"],
    };
    const { result } = renderHook(() => useAnalytics(query), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockedPost.mock.calls[0][0]).toBe(
      `/api/knowledge/v1/owners/${OWNER}/usage/analytics/query`,
    );
    // `group_by: ["knowledge_id"]` is the service's own per-collection answer.
    expect(mockedPost.mock.calls[0][1]).toEqual(query);
    expect(result.current.data?.totals.cost?.total_cost_usd).toBe(1.25);
  });

  it("does not fire a query the screen has switched away from", () => {
    renderHook(
      () =>
        useAnalytics(
          { date_range: { start: "2026-07-21", end: "2026-08-20" } },
          { enabled: false },
        ),
      { wrapper },
    );
    expect(mockedPost).not.toHaveBeenCalled();
  });

  it("reads the service catalogue against the owner", async () => {
    mockedGet.mockResolvedValue({
      data: {
        service_types: [
          {
            service_type: "chat",
            display_name: "Chat",
            operations: ["chat_completion"],
          },
        ],
        all_operations: ["chat_completion", "embedding_generation"],
      },
    });

    const { result } = renderHook(() => useServiceOptions(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockedGet.mock.calls[0][0]).toBe(
      `/api/knowledge/v1/owners/${OWNER}/usage/analytics/service-options`,
    );
  });
});

describe("the consumption source", () => {
  /**
   * Where `key_type` goes is the whole bug.
   *
   * It sat beside `date_range` as `key_filter`, which the route neither rejects
   * nor reads: every request came back 200 over the full window, so the control
   * looked like it worked and narrowed nothing.
   */
  it("files the key type inside filters", () => {
    expect(queryFilters(undefined, "owner")).toEqual({
      key_type: "owner-key",
    });
    expect(queryFilters(undefined, "platform")).toEqual({
      key_type: "platform-key",
    });
  });

  /**
   * Total is its own unfiltered call, never platform + owner. Embeddings and
   * vector writes carry no key type, so they answer to neither filter and the
   * sum undercounts — 267 + 2 against 336 requests, live on 2026-08-27.
   */
  it("sends nothing at all for Total", () => {
    expect(keyTypeFor("total")).toBeUndefined();
    expect(queryFilters(undefined, "total")).toBeUndefined();
  });

  /**
   * `undefined` rather than an empty object: an unfiltered query has to stay
   * byte-identical to the ones the other tabs send, or React Query asks the
   * same question twice under two cache keys.
   */
  it("carries the reader's own selections alongside it", () => {
    expect(queryFilters({ operations: ["llm_call"] }, "platform")).toEqual({
      operations: ["llm_call"],
      key_type: "platform-key",
    });
    expect(queryFilters({}, "total")).toBeUndefined();
  });
});

describe("the operation and label catalogue", () => {
  const options: ServiceOptions = {
    service_types: [
      {
        service_type: "knowledge",
        display_name: "Knowledge",
        operations: [
          {
            operation: "data_ingestion",
            display_name: "Data ingestion",
            labels: [
              { label: "file_processing", display_name: "File processing" },
            ],
          },
          {
            operation: "llm_generation",
            display_name: "LLM generation",
            labels: [
              { label: "generate_answer", display_name: "Generate answer" },
            ],
          },
        ],
      },
      // A service this product does not record; its operations must not leak
      // into a picker that files them against `knowledge` usage.
      {
        service_type: "nl2sql",
        display_name: "Database Agent",
        operations: [{ operation: "sql_generation", display_name: "SQL" }],
      },
    ],
    all_labels: ["file_processing", "generate_answer", "content_embedding"],
  };

  it("offers the knowledge service's operations, in the service's own words", () => {
    expect(operationChoices(options, undefined)).toEqual([
      { value: "data_ingestion", label: "Data ingestion" },
      { value: "llm_generation", label: "LLM generation" },
    ]);
  });

  it("narrows the labels to the chosen operation", () => {
    expect(labelChoices(options, "llm_generation")).toEqual([
      { value: "generate_answer", label: "Generate answer" },
    ]);
  });

  /**
   * Only what this service nests. The flat `all_labels` beside it is
   * platform-wide — `content_embedding` there belongs to a service type this
   * product does not record — and a label filed from it answers with an empty
   * window rather than a hit.
   */
  it("offers every nested label until one narrows them", () => {
    expect(labelChoices(options).map((choice) => choice.value)).toEqual([
      "file_processing",
      "generate_answer",
    ]);
  });

  /**
   * A catalogue that nests nothing leaves the picker empty, and the flat list —
   * imprecise as it is — beats a control with nothing in it.
   */
  it("falls back to the flat list when no operation nests a label", () => {
    const bare: ServiceOptions = {
      ...options,
      service_types: options.service_types.map((entry) => ({
        ...entry,
        operations: (entry.operations ?? []).map(
          ({ labels: _labels, ...rest }) => rest,
        ),
      })),
    };
    expect(labelChoices(bare).map((choice) => choice.value)).toEqual([
      "file_processing",
      "generate_answer",
      "content_embedding",
    ]);
    // Never against a chosen operation: the flat list carries no operation.
    expect(labelChoices(bare, "llm_generation")).toEqual([]);
  });

  it("names an operation the catalogue has never heard of by its own code", () => {
    const nameOf = operationLabeller(options);
    expect(nameOf("data_ingestion")).toBe("Data ingestion");
    expect(nameOf("vector_db_write")).toBe("vector_db_write");
    expect(nameOf(undefined)).toBe("—");
  });
});

describe("the period comparison", () => {
  /**
   * "Compared with the previous period of the same length" is a promise the
   * screen makes in words, so the two windows have to actually be adjacent and
   * the same length.
   */
  it("puts the previous window immediately before, at the same length", () => {
    const { current, previous } = periodRanges(
      "7d",
      new Date("2026-08-20T12:00:00Z"),
    );
    expect(current).toEqual({ start: "2026-08-13", end: "2026-08-20" });
    expect(previous).toEqual({ start: "2026-08-06", end: "2026-08-13" });
  });

  it("scales to the ninety-day window", () => {
    const { current, previous } = periodRanges(
      "90d",
      new Date("2026-08-20T12:00:00Z"),
    );
    expect(current.end).toBe("2026-08-20");
    expect(current.start).toBe("2026-05-22");
    expect(previous.end).toBe("2026-05-22");
    expect(previous.start).toBe("2026-02-21");
  });

  it("reports change, and reports nothing when there is nothing to compare", () => {
    expect(percentChange(150, 100)).toBeCloseTo(50);
    expect(percentChange(50, 100)).toBeCloseTo(-50);
    // Growth from zero is not "+100%" — the screen shows no chevron at all.
    expect(percentChange(10, 0)).toBeNull();
  });
});

describe("the limits strip", () => {
  const status = {
    owner_id: "owner@futuresmart.ai",
    period: { start: "2026-08-01", end: "2026-09-01" },
    limits: {
      max_agents: 10,
      max_indexed_characters: 1_000_000,
      max_knowledge_items: null,
      max_conversations_per_month: null,
      max_messages_per_month: 10_000,
      max_llm_tokens_per_month: 500_000,
      max_documents: 500,
      max_urls: null,
      max_messages_per_day: 200,
      max_cost_usd_per_month: 25,
    },
    current_usage: {
      indexed_characters: 250_000,
      knowledge_items: 12,
      agents: 4,
      documents: 90,
      urls: 8,
      conversations_this_month: 3,
      messages_this_month: 2_500,
      llm_tokens_this_month: 3_000,
      messages_today: 40,
      cost_usd_this_month: 3.8,
    },
    remaining: {
      indexed_characters: 750_000,
      knowledge_items: null,
      agents: 6,
      conversations: null,
      messages: 7_500,
      llm_tokens: 497_000,
      documents: 410,
      urls: null,
      messages_today: 160,
      cost_usd: 21.2,
    },
  };

  /** One row, by the cap it was built from. */
  const rowFor = (key: string) =>
    quotaRows(status).find((row) => row.key === key);

  /** Each cap is paired with its counter by name, monthly ones included. */
  it("pairs every cap the service returned with the counter it governs", () => {
    expect(quotaRows(status)).toEqual([
      { key: "max_agents", label: "Agents", used: 4, limit: 10 },
      {
        key: "max_indexed_characters",
        label: "Indexed characters",
        used: 250_000,
        limit: 1_000_000,
      },
      {
        key: "max_knowledge_items",
        label: "Knowledge items",
        used: 12,
        limit: null,
      },
      {
        key: "max_conversations_per_month",
        label: "Conversations",
        used: 3,
        limit: null,
      },
      {
        key: "max_messages_per_month",
        label: "Messages",
        used: 2_500,
        limit: 10_000,
      },
      {
        key: "max_llm_tokens_per_month",
        label: "LLM tokens",
        used: 3_000,
        limit: 500_000,
      },
      { key: "max_documents", label: "Documents", used: 90, limit: 500 },
      { key: "max_urls", label: "Urls", used: 8, limit: null },
      {
        key: "max_messages_per_day",
        label: "Messages today",
        used: 40,
        limit: 200,
      },
      {
        key: "max_cost_usd_per_month",
        label: "Cost usd",
        used: 3.8,
        limit: 25,
      },
    ]);
  });

  /** A cap the service adds needs no release here to be drawn. */
  it("draws a cap this app has never heard of, when its counter is there too", () => {
    const rows = quotaRows({
      ...status,
      limits: { ...status.limits, max_widgets_per_month: 7 },
      current_usage: { ...status.current_usage, widgets_this_month: 2 },
    } as unknown as Parameters<typeof quotaRows>[0]);
    expect(rows.at(-1)).toEqual({
      key: "max_widgets_per_month",
      label: "Widgets",
      used: 2,
      limit: 7,
    });
  });

  /** Without a counter there is no reading, and a bar would invent one. */
  it("leaves out a cap the answer counts nothing for", () => {
    const rows = quotaRows({
      ...status,
      limits: { ...status.limits, max_file_size_mb: 25 },
    } as unknown as Parameters<typeof quotaRows>[0]);
    expect(rows.some((row) => row.key === "max_file_size_mb")).toBe(false);
  });

  /**
   * A daily cap counts `messages_today`, not `messages_this_month`. Reading it
   * against the monthly counter would report a figure several times its own
   * window — plausible, and wrong in the direction that alarms the reader.
   */
  it("pairs a daily cap with the day's counter, not the month's", () => {
    // 40 asked today, 2,500 this month: a daily row reading the monthly
    // counter is plausible, and wrong in the direction that alarms the reader.
    expect(rowFor("max_messages_per_day")?.used).toBe(40);
    expect(rowFor("max_messages_per_month")?.used).toBe(2_500);
  });

  /** The estimated-spend cap is a month figure like the rest of them. */
  it("pairs the spend cap with this month's cost", () => {
    expect(rowFor("max_cost_usd_per_month")).toEqual({
      key: "max_cost_usd_per_month",
      label: "Cost usd",
      used: 3.8,
      limit: 25,
    });
  });

  it("has nothing to draw before the status arrives", () => {
    expect(quotaRows(undefined)).toEqual([]);
  });
});
