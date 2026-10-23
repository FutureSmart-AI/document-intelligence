"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { KNOWLEDGE_CONSTRAINTS } from "@/generated/knowledgeContract";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";
import { useOwnerId } from "./session";

/**
 * The provider keys this account answers with.
 *
 * Bring-your-own-key, stored per **account** — the service's own words are that
 * a stored key "applies to every knowledge base the owner has" — so nothing
 * here takes a collection, and the screen does not make you pick one. Anything
 * you have not added a key for keeps running on FutureSmart's platform
 * credentials.
 *
 * Keys are write-only: the value you type is encrypted on the way in and only
 * ever read back as a mask.
 */

export interface Credential {
  provider: string;
  /** e.g. `sk-…4f2a` — the service never returns the key itself. */
  mask: string;
  is_valid: boolean;
  updated_at: string;
}

export interface ProviderOptions {
  llm_providers: string[];
  document_and_web_providers: string[];
}

export interface ProviderGroup {
  label: string;
  providers: string[];
}

/**
 * The two groups the picker offers, named as the artifact names them: what
 * writes the answers, and what turns files and links into text.
 *
 * The providers themselves come from `credentials/providers` and nowhere else.
 * The service's own schema requires both arrays on that response, so a list
 * kept here as well would only be a second answer to a question the service has
 * already answered — and the one that goes stale when a provider is added.
 */
export function providerGroups(
  options?: ProviderOptions | undefined,
): ProviderGroup[] {
  return [
    { label: "Answer providers", providers: options?.llm_providers ?? [] },
    {
      label: "Document & website reading",
      providers: options?.document_and_web_providers ?? [],
    },
  ].filter((group) => group.providers.length > 0);
}

/** Every provider that can be given a key, in picker order. */
export function allProviders(options?: ProviderOptions | undefined): string[] {
  return providerGroups(options).flatMap((group) => group.providers);
}

/**
 * The shortest key the service will store.
 *
 * Read from the generated contract rather than typed here: `api_key` carries
 * `minLength` on the service's own request schema, so anything shorter is a 422
 * rather than a saved credential.
 */
export const MIN_API_KEY_LENGTH =
  KNOWLEDGE_CONSTRAINTS.credentialApiKeyMinLength;

/** The providers this account has a key for, masked. */
export function useCredentials(): UseQueryResult<Credential[]> {
  const ownerId = useOwnerId();
  return useQuery<Credential[]>({
    queryKey: ["di-credentials", ownerId ?? null],
    enabled: Boolean(ownerId),
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const response = await axios.get<{ data?: Credential[] }>(
        buildApiUrl(APIS.CREDENTIALS, { owner_id: ownerId as string }),
      );
      return response.data?.data ?? [];
    },
  });
}

/** Which providers can be given a key at all. */
export function useCredentialProviders(): UseQueryResult<ProviderOptions> {
  const ownerId = useOwnerId();
  return useQuery<ProviderOptions>({
    queryKey: ["di-credential-providers", ownerId ?? null],
    enabled: Boolean(ownerId),
    refetchOnWindowFocus: false,
    staleTime: 60 * 60 * 1000,
    queryFn: async () => {
      const response = await axios.get<ProviderOptions>(
        buildApiUrl(APIS.CREDENTIAL_PROVIDERS, { owner_id: ownerId as string }),
      );
      return (
        response.data ?? { llm_providers: [], document_and_web_providers: [] }
      );
    },
  });
}

/** Stores a key, replacing whatever that provider had. */
export function useSaveCredential() {
  const ownerId = useOwnerId();
  const client = useQueryClient();
  return useMutation<
    { status?: string; data?: Credential },
    unknown,
    { provider: string; apiKey: string }
  >({
    mutationFn: async ({ provider, apiKey }) => {
      const response = await axios.post(
        buildApiUrl(APIS.CREDENTIALS, { owner_id: ownerId as string }),
        { provider, api_key: apiKey },
      );
      return response.data;
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["di-credentials", ownerId] });
    },
  });
}

/** Removes one provider's key, putting that service back on the platform's. */
export function useDeleteCredential() {
  const ownerId = useOwnerId();
  const client = useQueryClient();
  return useMutation<{ status?: string; message?: string }, unknown, string>({
    mutationFn: async (provider) => {
      const response = await axios.delete(
        buildApiUrl(APIS.CREDENTIAL, {
          owner_id: ownerId as string,
          provider,
        }),
      );
      return response.data;
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["di-credentials", ownerId] });
    },
  });
}
