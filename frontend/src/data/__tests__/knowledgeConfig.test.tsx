import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "@/platform/utils/axios";

import {
  mergeKnowledgeConfig,
  useKnowledgeSettings,
  useSaveKnowledgeSettings,
  type KnowledgeConfig,
} from "../knowledgeConfig";

vi.mock("@/platform/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const mockedGet = vi.mocked(axios.get);
const mockedPut = vi.mocked(axios.put);

const K = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";

const stored: KnowledgeConfig = {
  collection_name: "Resumes",
  service_description: "probe",
  embedding_model: "openai-text-embedding-3-large",
  hybrid_search_enabled: false,
  chunking_strategy: {
    document_level_metadata: true,
    chunk_level_metadata: true,
  },
  use_deterministic_chunking: true,
  enrichment_enabled: false,
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useKnowledgeSettings", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads the knowledge base's config", async () => {
    mockedGet.mockResolvedValue({ data: stored });

    const { result } = renderHook(() => useKnowledgeSettings(K), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(mockedGet).toHaveBeenCalledWith(`/api/knowledge/v1/${K}/config`);
    expect(result.current.data).toMatchObject({ collection_name: "Resumes" });
  });
});

/**
 * What the Settings API contract allows this product to send: the name and
 * description someone typed, plus `enrichment_enabled`.
 *
 * The indexing and extraction machinery is the backend's, and the route still
 * accepts those fields — so sending one would pin today's default into a
 * released UI. The extraction flag is the one field still worth carrying over,
 * since the route defaults it: saving a description must not turn extraction
 * back on.
 */
describe("mergeKnowledgeConfig", () => {
  it("carries the extraction flag over from the stored config", () => {
    expect(
      mergeKnowledgeConfig(stored, { service_description: "next" }),
    ).toEqual({
      service_description: "next",
      enrichment_enabled: false,
    });
  });

  it("sends nothing the backend owns, even when asked to", () => {
    const merged = mergeKnowledgeConfig(stored, {
      service_description: "next",
      embedding_model: "openai-text-embedding-3-large",
      hybrid_search_enabled: true,
      chunking_strategy: { document_level_metadata: true },
      use_deterministic_chunking: true,
      enrichment_provider: "openai",
      enrichment_model: "gpt-5.6-luna",
      enrichment_temperature: 0.2,
      enrichment_prompt: "extract",
    });
    expect(merged).toEqual({
      service_description: "next",
      enrichment_enabled: false,
    });
  });

  it("lets the patch win over the stored value", () => {
    const merged = mergeKnowledgeConfig(stored, { enrichment_enabled: true });
    expect(merged.enrichment_enabled).toBe(true);
  });

  it("does not invent values when nothing is stored yet", () => {
    expect(mergeKnowledgeConfig(undefined, { collection_name: "New" })).toEqual(
      {
        collection_name: "New",
      },
    );
  });

  /** The name and description are the caller's own; they are never back-filled. */
  it("leaves the name and description to the caller", () => {
    const merged = mergeKnowledgeConfig(stored, { enrichment_enabled: true });
    expect(merged).not.toHaveProperty("collection_name");
    expect(merged).not.toHaveProperty("service_description");
  });
});

describe("useSaveKnowledgeSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedPut.mockResolvedValue({
      data: { knowledge_id: K, message: "Configuration saved successfully" },
    });
  });

  it("puts the patch merged over the config it holds", async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(["di-knowledge-config", K], stored);

    const { result } = renderHook(() => useSaveKnowledgeSettings(K), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    });
    await result.current.mutateAsync({ service_description: "next" });

    expect(mockedPut).toHaveBeenCalledWith(`/api/knowledge/v1/${K}/config`, {
      service_description: "next",
      enrichment_enabled: false,
    });
  });
});
