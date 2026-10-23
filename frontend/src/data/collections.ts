"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";

import type { IconName } from "@/components/Icon";
import { collectionIcon, type DocumentLike } from "@/lib/documentIcon";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";
import { useOwnerId } from "./session";

/**
 * A collection, as this product talks about it.
 *
 * One collection is one knowledge base. The knowledge service owns all of it —
 * the documents, the extracted fields, the answer settings and the
 * conversations — so the id in `?collection=` is a `knowledge_id` and there is
 * no agent anywhere in this product.
 */
export interface Collection {
  /** The knowledge id, carried in every route as `?collection=`. */
  id: string;
  name: string;
  /**
   * The same id again. Kept as its own field because the views mounted from the
   * agent UI read `knowledgeId` from the auth context, and naming it here keeps
   * the two ideas legible where a screen means "the documents" rather than
   * "the collection".
   */
  knowledgeId: string;
  /**
   * Whether questions are answered — `chat_enabled` on the chat config. Only
   * `useCollection` knows it (that is a second request per collection); the
   * board leaves it true rather than paying for it once per card.
   */
  knowledgeEnabled: boolean;
  /** `service_description` — the collection's context sentence. */
  about: string | null;
  /**
   * True when this account is not the collection's owner — somebody invited it.
   *
   * Read from `GET …/validate?owner_id=…`, the one route that answers who owns
   * a collection (see `data/api.ts`). Defaults to false, so a validate call
   * that cannot be made leaves the board exactly as it was.
   */
  shared: boolean;
  updatedAt: string | null;
  createdAt: string | null;
}

/** `GET /api/knowledge/v1/{knowledge_id}/config`. */
export interface KnowledgeConfigWire {
  knowledge_id?: string;
  collection_name?: string | null;
  service_description?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export function toCollection(
  knowledgeId: string,
  config: KnowledgeConfigWire | null,
  options?: { knowledgeEnabled?: boolean; shared?: boolean },
): Collection {
  return {
    id: knowledgeId,
    knowledgeId,
    name: config?.collection_name || "Untitled collection",
    knowledgeEnabled: options?.knowledgeEnabled ?? true,
    about: config?.service_description ?? null,
    shared: options?.shared ?? false,
    updatedAt: config?.updated_at ?? config?.created_at ?? null,
    createdAt: config?.created_at ?? null,
  };
}

const configUrl = (knowledgeId: string) =>
  buildApiUrl(APIS.KNOWLEDGE_CONFIG, { knowledge_id: knowledgeId });

/** `GET /api/knowledge/v1?owner_id=…` — ids only, which is all the route returns. */
async function listKnowledgeIds(ownerId: string): Promise<string[]> {
  const response = await axios.get<{ knowledge_services?: string[] }>(
    APIS.KNOWLEDGE,
    { params: { owner_id: ownerId } },
  );
  return response.data?.knowledge_services ?? [];
}

/**
 * Whether each id belongs to this account, in one pass.
 *
 * `GET …/validate` answers `{ valid }` for one collection at a time, so this is
 * one small request per collection — the same shape as the config reads it runs
 * beside. A request that fails answers `true`: the board has always treated
 * everything it lists as the account's own, and a validate outage must not move
 * cards to another tab.
 */
async function ownershipByKnowledgeId(
  ids: string[],
  ownerId: string,
): Promise<Record<string, boolean>> {
  const results = await Promise.allSettled(
    ids.map((knowledge_id) =>
      axios.get<{ valid?: boolean }>(APIS.KNOWLEDGE_VALIDATE, {
        params: { owner_id: ownerId, knowledge_id },
      }),
    ),
  );

  return Object.fromEntries(
    ids.map((id, index) => {
      const result = results[index];
      if (result.status !== "fulfilled") return [id, true];
      return [id, result.value.data?.valid !== false];
    }),
  );
}

/**
 * Every collection this account can reach, newest first — the ones it owns and
 * the ones people shared with it, each flagged by `shared`.
 *
 * The list route answers ids and nothing else — no name, no timestamp — so each
 * id's config is read for the two things a card shows. A knowledge base with no
 * `collection_name` is not a collection: it is a knowledge base created
 * somewhere else in the platform, and showing it here as "Untitled" would put
 * every RAG agent's documents on this board. Naming one is what adopts it, and
 * that is what the create dialog does.
 */
export function useCollections() {
  const ownerId = useOwnerId();

  const query = useQuery<Collection[]>({
    queryKey: ["di-collections", ownerId],
    enabled: Boolean(ownerId),
    refetchOnWindowFocus: false,
    staleTime: 60 * 1000,
    queryFn: async () => {
      const ids = await listKnowledgeIds(ownerId as string);
      // One config read per id, all at once. A single unreadable config (a
      // knowledge base mid-delete answers 403/404) must not empty the board, so
      // the failures are dropped rather than thrown.
      const [configs, owned] = await Promise.all([
        Promise.allSettled(
          ids.map((id) => axios.get<KnowledgeConfigWire>(configUrl(id))),
        ),
        ownershipByKnowledgeId(ids, ownerId as string),
      ]);

      return ids
        .map((id, index) => {
          const result = configs[index];
          if (result.status !== "fulfilled") return null;
          const config = result.value.data ?? null;
          if (!config?.collection_name) return null;
          return toCollection(id, config, { shared: owned[id] === false });
        })
        .filter((collection): collection is Collection => Boolean(collection))
        .sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""));
    },
  });

  return { ...query, collections: query.data ?? [] };
}

/**
 * One collection by id, for the collection-scoped views.
 *
 * An id this account cannot see answers 403 or 404, which retrying cannot
 * change — and a query left in an error state holds no data, so every consumer
 * that mounts asks again. Resolving to null caches the answer.
 */
export function useCollection(collectionId?: string) {
  const query = useQuery<Collection | null>({
    queryKey: ["di-collection", collectionId ?? null],
    enabled: Boolean(collectionId),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const knowledgeId = collectionId as string;
      try {
        // The name and description are on the knowledge config; whether
        // questions are answered at all is on the chat config. A collection is
        // still readable when the chat config is not, so only the first is
        // allowed to decide the collection exists.
        const [config, chatConfig] = await Promise.all([
          axios.get<KnowledgeConfigWire>(configUrl(knowledgeId)),
          axios
            .get<{
              chat_enabled?: boolean;
            }>(buildApiUrl(APIS.CHAT_CONFIG, { knowledge_id: knowledgeId }))
            .catch(() => null),
        ]);
        if (!config.data) return null;
        return toCollection(knowledgeId, config.data, {
          knowledgeEnabled: chatConfig?.data?.chat_enabled ?? true,
        });
      } catch (error) {
        const status = isAxiosError(error) ? error.response?.status : undefined;
        if (status === 403 || status === 404) return null;
        throw error;
      }
    },
  });

  return { ...query, collection: query.data ?? null };
}

export interface CollectionStats {
  documents: number;
  processing: number;
  failed: number;
  /** Extracted fields defined on the knowledge base, document + section levels. */
  fields: number;
  /** Fields the account added, as opposed to the built-in system ones. */
  customFields: number;
  /**
   * The mark the card wears, from what the collection actually holds — see
   * `lib/documentIcon`. Derived here because the documents are already in hand.
   */
  icon: IconName;
}

const PROCESSING_STATUSES = new Set([
  "processing",
  "pending",
  "in_progress",
  "queued",
]);

/**
 * The three numbers a collection card shows.
 *
 * Deliberately one query per collection rather than one aggregate call: there is
 * no aggregate route, and a card that waits for every other card's documents
 * before showing its own would make the whole board as slow as its largest
 * collection.
 */
export function useCollectionStats(
  knowledgeId: string | null,
  /** The collection's name — half of what decides its mark. */
  name = "",
) {
  return useQuery<CollectionStats>({
    queryKey: ["di-collection-stats", knowledgeId, name],
    enabled: Boolean(knowledgeId),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 60 * 1000,
    queryFn: async () => {
      const documentsUrl = buildApiUrl(APIS.DOCUMENTS, {
        knowledge_id: knowledgeId as string,
      });
      const enrichmentUrl = buildApiUrl(APIS.ENRICHMENT, {
        knowledge_id: knowledgeId as string,
      });

      const [documentsResult, enrichmentResult] = await Promise.allSettled([
        axios.get(documentsUrl),
        axios.get(enrichmentUrl),
      ]);

      const payload =
        documentsResult.status === "fulfilled"
          ? documentsResult.value.data
          : null;
      const documents: Array<DocumentLike & { status?: string }> =
        Array.isArray(payload)
          ? payload
          : (payload?.documents ?? payload?.document ?? []);

      // The documents route is paginated — the array is one page of twenty, and
      // `total_count` is how many there are. Counting the array made a
      // collection of two hundred documents read as twenty.
      const total =
        typeof payload?.total_count === "number"
          ? payload.total_count
          : documents.length;

      const fields: Array<{ is_custom?: boolean }> =
        enrichmentResult.status === "fulfilled"
          ? (enrichmentResult.value.data?.fields ?? [])
          : [];

      return {
        documents: total,
        processing: documents.filter((doc) =>
          PROCESSING_STATUSES.has(String(doc?.status ?? "").toLowerCase()),
        ).length,
        failed: documents.filter(
          (doc) => String(doc?.status ?? "").toLowerCase() === "failed",
        ).length,
        fields: fields.length,
        customFields: fields.filter((field) => field?.is_custom !== false)
          .length,
        icon: collectionIcon(name, documents),
      };
    },
  });
}

/**
 * Removes a collection this account owns.
 *
 * `DELETE /v1/{knowledge_id}` is the knowledge service's own teardown — the
 * documents, extracted fields and conversations go with it. Shared cards never
 * call this: the board does not offer a delete the account is not allowed to
 * finish.
 */
export function useDeleteCollection() {
  const client = useQueryClient();
  return useMutation<void, unknown, Collection>({
    mutationFn: async (collection) => {
      await axios.delete(
        buildApiUrl(APIS.KNOWLEDGE_ITEM, {
          knowledge_id: collection.knowledgeId,
        }),
      );
    },
    onSuccess: (_data, collection) => {
      client.invalidateQueries({ queryKey: ["di-collections"] });
      client.invalidateQueries({
        queryKey: ["di-collection", collection.knowledgeId],
      });
      client.invalidateQueries({
        queryKey: ["di-collection-stats", collection.knowledgeId],
      });
    },
  });
}
