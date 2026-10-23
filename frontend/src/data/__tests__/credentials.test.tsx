import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KNOWLEDGE_CONSTRAINTS } from "@/generated/knowledgeContract";
import axios from "@/platform/utils/axios";

import {
  allProviders,
  MIN_API_KEY_LENGTH,
  providerGroups,
  useCredentialProviders,
  useCredentials,
  useDeleteCredential,
  useSaveCredential,
} from "../credentials";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("../session", () => ({
  useOwnerId: () => "owner@futuresmart.ai",
}));

const mockedGet = vi.mocked(axios.get);
const mockedPost = vi.mocked(axios.post);
const mockedDelete = vi.mocked(axios.delete);

// `buildApiUrl` substitutes the id verbatim, and the service matches the raw
// `@` in the path — an encoded one is not what the app actually sends.
const OWNER = "owner@futuresmart.ai";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const PROVIDERS = {
  llm_providers: ["openai", "anthropic"],
  document_and_web_providers: ["llamaparse", "firecrawl"],
};

/**
 * Keys are stored per account, not per collection.
 *
 * The service is explicit — a stored key "applies to every knowledge base the
 * owner has" — and the app used to send these to a knowledge-scoped path that
 * does not exist. The URL each call makes is therefore what these assert.
 */
describe("credentials belong to the account", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists the account's keys, masked", async () => {
    mockedGet.mockResolvedValue({
      data: {
        data: [
          {
            provider: "openai",
            mask: "sk-…4f2a",
            is_valid: true,
            updated_at: "2026-08-18T18:14:51.376203",
          },
        ],
      },
    });

    const { result } = renderHook(() => useCredentials(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockedGet.mock.calls[0][0]).toBe(
      `/api/knowledge/v1/owners/${OWNER}/usage/credentials`,
    );
    expect(result.current.data?.[0].mask).toBe("sk-…4f2a");
  });

  it("reads the provider catalogue against the owner", async () => {
    mockedGet.mockResolvedValue({ data: PROVIDERS });
    const { result } = renderHook(() => useCredentialProviders(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockedGet.mock.calls[0][0]).toBe(
      `/api/knowledge/v1/owners/${OWNER}/usage/credentials/providers`,
    );
  });

  it("posts a key as `api_key`, to the owner's route", async () => {
    mockedPost.mockResolvedValue({ data: { status: "success" } });
    const { result } = renderHook(() => useSaveCredential(), { wrapper });

    await result.current.mutateAsync({
      provider: "anthropic",
      apiKey: "sk-ant-not-a-real-key",
    });

    expect(mockedPost.mock.calls[0][0]).toBe(
      `/api/knowledge/v1/owners/${OWNER}/usage/credentials`,
    );
    expect(mockedPost.mock.calls[0][1]).toEqual({
      provider: "anthropic",
      api_key: "sk-ant-not-a-real-key",
    });
  });

  it("deletes one provider's key by name", async () => {
    mockedDelete.mockResolvedValue({ data: { message: "Credential removed" } });
    const { result } = renderHook(() => useDeleteCredential(), { wrapper });

    await result.current.mutateAsync("openai");

    expect(mockedDelete.mock.calls[0][0]).toBe(
      `/api/knowledge/v1/owners/${OWNER}/usage/credentials/openai`,
    );
  });
});

describe("the provider picker", () => {
  /**
   * The picker is the catalogue and nothing else.
   *
   * A second copy of the provider list kept in the app would answer a question
   * the service already answers, and would be the copy that goes stale the day
   * a provider is added — so with no catalogue there is nothing to offer, and
   * the dialog says so rather than inventing a list.
   */
  it("offers nothing until the catalogue answers", () => {
    expect(providerGroups(undefined)).toEqual([]);
    expect(allProviders(undefined)).toEqual([]);
  });

  it("groups the catalogue's own answer under the dialog's labels", () => {
    expect(providerGroups(PROVIDERS)).toEqual([
      { label: "Answer providers", providers: ["openai", "anthropic"] },
      {
        label: "Document & website reading",
        providers: ["llamaparse", "firecrawl"],
      },
    ]);
  });

  /** A provider the service adds needs no release here to be offered. */
  it("offers whatever the service returns, including names it has just added", () => {
    expect(
      allProviders({
        llm_providers: ["openai", "some-new-provider"],
        document_and_web_providers: [],
      }),
    ).toEqual(["openai", "some-new-provider"]);
  });

  /** A group the service answers empty loses its heading rather than showing bare. */
  it("drops a group the service returned nothing for", () => {
    expect(
      providerGroups({
        llm_providers: ["openai"],
        document_and_web_providers: [],
      }),
    ).toEqual([{ label: "Answer providers", providers: ["openai"] }]);
  });

  /** The rule is the service's own, read from the generated contract. */
  it("takes the shortest storable key from the published schema", () => {
    expect(MIN_API_KEY_LENGTH).toBe(
      KNOWLEDGE_CONSTRAINTS.credentialApiKeyMinLength,
    );
  });
});
