"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/** `GET /api/knowledge/v1/{knowledge_id}/config`. */
export interface KnowledgeConfig {
  /** The collection's display name — what the board and the rail show. */
  collection_name?: string | null;
  /** "What are these documents about?" — the collection's context sentence. */
  service_description?: string | null;
  /**
   * Read-only here on down: the indexing and extraction machinery is the
   * backend's own, and this product offers no control over any of it. The route
   * still accepts these fields, so they are kept for what `GET …/config`
   * answers — `stripBackendOwned` keeps them out of every body we send.
   */
  embedding_model?: string;
  hybrid_search_enabled?: boolean;
  chunking_strategy?: {
    document_level_metadata?: boolean;
    chunk_level_metadata?: boolean;
  };
  use_deterministic_chunking?: boolean;
  enrichment_enabled?: boolean;
  enrichment_provider?: string | null;
  enrichment_model?: string | null;
  enrichment_temperature?: number | null;
  enrichment_prompt?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

const configUrl = (knowledgeId: string) =>
  buildApiUrl(APIS.KNOWLEDGE_CONFIG, { knowledge_id: knowledgeId });

/** The knowledge base's own config — its name, its context sentence, its indexing. */
export function useKnowledgeSettings(knowledgeId: string | null) {
  return useQuery<KnowledgeConfig>({
    queryKey: ["di-knowledge-config", knowledgeId],
    enabled: Boolean(knowledgeId),
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const response = await axios.get<KnowledgeConfig>(
        configUrl(knowledgeId as string),
      );
      return response.data ?? {};
    },
  });
}

/**
 * What `PUT …/config` is allowed to carry, per the Settings API contract: the
 * name and description someone typed, plus `enrichment_enabled`.
 *
 * Everything else on the config is the backend's to decide — the embedding
 * model, hybrid search, the metadata levels, deterministic chunking and the
 * extraction LLM. The route still accepts them, which is exactly why they are
 * stripped here rather than merely left out of the screens: a value sent once
 * freezes today's default into a released UI.
 */
const BACKEND_OWNED = [
  "embedding_model",
  "hybrid_search_enabled",
  "chunking_strategy",
  "use_deterministic_chunking",
  "enrichment_provider",
  "enrichment_model",
  "enrichment_temperature",
  "enrichment_prompt",
] as const satisfies ReadonlyArray<keyof KnowledgeConfig>;

/**
 * The one field the route still defaults rather than leaves alone, and the one
 * this product owns: saving a description must not turn extraction back on.
 */
const PRESERVED_FIELDS = [
  "enrichment_enabled",
] as const satisfies ReadonlyArray<keyof KnowledgeConfig>;

/** Drops the fields the backend owns, whatever a caller passed. */
function stripBackendOwned(patch: KnowledgeConfig): KnowledgeConfig {
  const body: KnowledgeConfig = { ...patch };
  for (const field of BACKEND_OWNED) delete body[field];
  return body;
}

/**
 * Builds the body for a partial save: the caller's own fields, minus anything
 * the backend owns, plus the extraction flag as stored when the caller is not
 * the screen that changes it.
 */
export function mergeKnowledgeConfig(
  current: KnowledgeConfig | undefined,
  patch: KnowledgeConfig,
): KnowledgeConfig {
  const body = stripBackendOwned(patch);
  if (!current) return body;
  for (const field of PRESERVED_FIELDS) {
    if (body[field] === undefined && current[field] !== undefined) {
      // Reading a per-field union back into the same key needs one cast; every
      // value here comes from the same shape it is written to.
      (body as Record<string, unknown>)[field] = current[field];
    }
  }
  return body;
}

/**
 * Saves the knowledge config.
 *
 * Takes only the fields being changed and merges them over what is stored — see
 * `mergeKnowledgeConfig` for why the merge cannot be left to the service.
 */
export function useSaveKnowledgeSettings(knowledgeId: string | null) {
  const client = useQueryClient();
  return useMutation<{ message?: string }, unknown, KnowledgeConfig>({
    mutationFn: async (patch) => {
      const current = client.getQueryData<KnowledgeConfig>([
        "di-knowledge-config",
        knowledgeId,
      ]);
      const response = await axios.put(
        configUrl(knowledgeId as string),
        mergeKnowledgeConfig(current, patch),
      );
      return response.data;
    },
    onSuccess: () => {
      client.invalidateQueries({
        queryKey: ["di-knowledge-config", knowledgeId],
      });
      client.invalidateQueries({
        queryKey: ["di-collection-stats", knowledgeId],
      });
      client.invalidateQueries({ queryKey: ["di-collection", knowledgeId] });
      client.invalidateQueries({ queryKey: ["di-collections"] });
    },
  });
}
