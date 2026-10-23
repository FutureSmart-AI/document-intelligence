import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import {
  useAnswerModels,
  useCollectionSettings,
  useSaveCollectionSettings,
  type CollectionSettings,
} from "../chatConfig";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const mockedGet = vi.mocked(axios.get);
const mockedPut = vi.mocked(axios.put);

const K = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** `GET …/chat/config` on a collection that has never been configured. */
const unconfigured = {
  knowledge_id: K,
  configured: false,
  llm_provider: "openai",
  llm_model: "gpt-5.4-mini",
  temperature: 0,
  max_tokens: 1024,
  show_sources: true,
  chat_enabled: true,
  follow_up: true,
  suggested_questions: [],
  voice_input_enabled: false,
  voice_output_enabled: false,
  include_charts: true,
  show_raw_response: true,
};

describe("useCollectionSettings", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads the collection's own chat config", async () => {
    mockedGet.mockResolvedValue({ data: unconfigured });

    const { result } = renderHook(() => useCollectionSettings(K), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(mockedGet).toHaveBeenCalledWith(
      `/api/knowledge/v1/${K}/chat/config`,
    );
    // `configured: false` means every value is a service default rather than a
    // stored one, and the screens still show them.
    expect(result.current.data).toMatchObject({
      configured: false,
      llm_model: "gpt-5.4-mini",
      show_sources: true,
    });
  });

  it("asks nothing without a collection", async () => {
    renderHook(() => useCollectionSettings(undefined), { wrapper });
    expect(mockedGet).not.toHaveBeenCalled();
  });
});

describe("useSaveCollectionSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedPut.mockResolvedValue({
      data: {
        knowledge_id: K,
        message: "Chat configuration saved successfully",
      },
    });
  });

  /**
   * `PUT …/chat/config` merges the body over what is stored and applies no
   * defaults to what is left out — measured: a body of `{follow_up: false}` left
   * the model, the temperature and the suggested questions exactly as they were.
   * So the settings screen may send only the fields it owns, flat.
   */
  it("puts the settings as flat fields on the chat config", async () => {
    const settings: CollectionSettings = {
      show_sources: true,
      follow_up: false,
      include_charts: true,
      show_raw_response: false,
      voice_input_enabled: true,
      voice_output_enabled: false,
      suggested_questions: ["What is this about?"],
      llm_provider: "anthropic",
      llm_model: "claude-sonnet-4-6",
      temperature: 0.3,
      max_tokens: 2048,
    };

    const { result } = renderHook(() => useSaveCollectionSettings(K), {
      wrapper,
    });
    const response = await result.current.mutateAsync(settings);

    expect(response?.message).toBe("Chat configuration saved successfully");
    expect(mockedPut).toHaveBeenCalledWith(
      `/api/knowledge/v1/${K}/chat/config`,
      settings,
    );
  });

  /**
   * The chat service kept the model, temperature and token cap nested under
   * `default_response_node`; this service keeps them flat and under different
   * names. Sending the old shape would be accepted and stored nowhere.
   */
  it("never sends the chat service's nested answer node", async () => {
    const { result } = renderHook(() => useSaveCollectionSettings(K), {
      wrapper,
    });
    await result.current.mutateAsync({ llm_model: "gpt-4o" });

    const [, body] = mockedPut.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(body).not.toHaveProperty("default_response_node");
    expect(body).not.toHaveProperty("agent_name");
    expect(body).toEqual({ llm_model: "gpt-4o" });
  });
});

describe("useAnswerModels", () => {
  beforeEach(() => vi.clearAllMocks());

  /** This service publishes its own catalogue; the chat providers route is gone. */
  it("reads the knowledge service's chat models", async () => {
    mockedGet.mockResolvedValue({
      data: {
        providers: [
          {
            provider_name: "openai",
            models: [{ model_name: "gpt-5.6-luna" }, { model_name: "gpt-4o" }],
            default_model: "gpt-5.6-luna",
          },
          {
            provider_name: "anthropic",
            models: [{ model_name: "claude-sonnet-4-6" }],
            default_model: "claude-sonnet-4-6",
          },
        ],
      },
    });

    const { result } = renderHook(() => useAnswerModels(), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(mockedGet).toHaveBeenCalledWith("/api/knowledge/v1/chat-models");
    expect(result.current.data).toEqual([
      {
        provider: "openai",
        defaultModel: "gpt-5.6-luna",
        models: ["gpt-5.6-luna", "gpt-4o"],
      },
      {
        provider: "anthropic",
        defaultModel: "claude-sonnet-4-6",
        models: ["claude-sonnet-4-6"],
      },
    ]);
  });

  it("answers an empty list rather than throwing on an empty catalogue", async () => {
    mockedGet.mockResolvedValue({ data: {} });

    const { result } = renderHook(() => useAnswerModels(), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual([]));
  });
});
