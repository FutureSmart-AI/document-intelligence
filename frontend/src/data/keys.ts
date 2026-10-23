"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * API keys, so someone's own code can ask a collection questions.
 *
 * - `GET  /v1/api-keys`         → `{ api_keys: [{ key_prefix, knowledge_id, … }] }`
 * - `POST /v1/api-keys`         → the **full key, once**, plus its prefix
 * - `POST /v1/api-keys/revoke`  → `{ key_prefix }`
 *
 * The routes are account-scoped and the collection is a **body** field, not a
 * path segment: a key minted with `knowledge_id` may only read that collection,
 * and one minted without it reaches every collection the account owns. The list
 * carries each key's `knowledge_id`, which is what lets the Share screen show
 * only the keys that apply to the collection being shared.
 *
 * The secret is returned exactly once, by the create call. Nothing else on the
 * service can produce it again, so the UI must show it at that moment and say
 * so — there is no "reveal" to build later.
 */

export interface ApiKey {
  key_prefix: string;
  knowledge_id?: string | null;
  is_active: boolean;
  created_at: string;
  revoked_at?: string | null;
}

export interface CreatedApiKey {
  api_key: string;
  key_prefix: string;
  created_at: string;
  user_id: string;
}

export function useApiKeys() {
  return useQuery({
    queryKey: ["api-keys"],
    queryFn: async (): Promise<ApiKey[]> => {
      const response = await axios.get<{ api_keys?: ApiKey[] }>(APIS.API_KEYS);
      return response.data.api_keys ?? [];
    },
  });
}

export function useApiKeyMutations() {
  const client = useQueryClient();
  const invalidate = () => client.invalidateQueries({ queryKey: ["api-keys"] });

  const create = useMutation({
    mutationFn: async (knowledgeId?: string): Promise<CreatedApiKey> => {
      const response = await axios.post<CreatedApiKey>(
        APIS.API_KEYS,
        // Omitted rather than null when there is no collection: an account-wide
        // key is what an absent field means, and the two are different grants.
        knowledgeId ? { knowledge_id: knowledgeId } : {},
      );
      return response.data;
    },
    onSuccess: invalidate,
  });

  const revoke = useMutation({
    mutationFn: async (keyPrefix: string) => {
      await axios.post(APIS.API_KEY_REVOKE, { key_prefix: keyPrefix });
    },
    onSuccess: invalidate,
  });

  return { create, revoke };
}
