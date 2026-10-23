"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * How a collection answers, as the knowledge service stores it.
 *
 * These are the fields of `GET/PUT /api/knowledge/v1/{knowledge_id}/chat/config`
 * this product sets. The service keeps more (per-node overrides, the system
 * prompt); they are left alone rather than sent as undefined, since the route
 * merges what it is given.
 */
export interface CollectionSettings {
  show_sources?: boolean;
  follow_up?: boolean;
  /** "Include charts when useful" — markdown tables for numeric answers. */
  include_charts?: boolean;
  /** "Show raw response" — the debug block under each answer. */
  show_raw_response?: boolean;
  /**
   * Voice preferences. The knowledge service has no speech pipeline of its own:
   * these gate what the chat UI offers, nothing server-side.
   */
  voice_input_enabled?: boolean;
  voice_output_enabled?: boolean;
  suggested_questions?: string[];
  llm_provider?: string;
  llm_model?: string;
  temperature?: number;
  max_tokens?: number;
  /** False makes `POST …/chat` answer 404 — the collection stops answering. */
  chat_enabled?: boolean;
  /**
   * The two hosted surfaces, each switched on independently — the service is
   * explicit that toggling one never affects the other.
   *
   * `is_public` is the website bubble; `fullscreen_is_public` is the full-page
   * link. Each carries its own allowlist of hostnames that may load it, and an
   * empty or absent list means any origin once the flag is on. Bare lowercase
   * hostnames, with `*.example.com` covering subdomains.
   */
  is_public?: boolean;
  allowed_domains?: string[];
  fullscreen_is_public?: boolean;
  fullscreen_allowed_domains?: string[];
}

/** Everything `GET …/chat/config` answers, including what this product reads only. */
export interface CollectionSettingsResponse extends CollectionSettings {
  knowledge_id?: string;
  /** False means every value above is a service default, not a stored one. */
  configured?: boolean;
}

const chatConfigUrl = (knowledgeId: string) =>
  buildApiUrl(APIS.CHAT_CONFIG, { knowledge_id: knowledgeId });

/** One collection's answer settings. */
export function useCollectionSettings(collectionId?: string) {
  return useQuery<CollectionSettingsResponse>({
    queryKey: ["di-collection-settings", collectionId ?? null],
    enabled: Boolean(collectionId),
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const response = await axios.get<CollectionSettingsResponse>(
        chatConfigUrl(collectionId as string),
      );
      return response.data ?? {};
    },
  });
}

/**
 * Saves answer settings.
 *
 * `PUT …/chat/config` merges the body over what is stored and applies no
 * defaults to the fields left out (measured: a body of `{follow_up: false}`
 * left the model, temperature and suggested questions exactly as they were), so
 * a screen may send only the settings it owns.
 */
export function useSaveCollectionSettings(collectionId?: string) {
  const client = useQueryClient();
  return useMutation<{ message?: string }, unknown, CollectionSettings>({
    mutationFn: async (settings) => {
      const response = await axios.put(
        chatConfigUrl(collectionId as string),
        settings,
      );
      return response.data;
    },
    onSuccess: () => {
      client.invalidateQueries({
        queryKey: ["di-collection-settings", collectionId ?? null],
      });
      client.invalidateQueries({
        queryKey: ["di-collection", collectionId ?? null],
      });
      client.invalidateQueries({ queryKey: ["di-collections"] });
    },
  });
}

interface ChatModelsResponse {
  providers?: Array<{
    provider_name: string;
    default_model?: string;
    models?: Array<{ model_name: string }>;
  }>;
}

/** The answer models the knowledge service currently exposes, by provider. */
export function useAnswerModels() {
  return useQuery({
    queryKey: ["di-answer-models"],
    staleTime: 30 * 60 * 1000,
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const response = await axios.get<ChatModelsResponse>(APIS.CHAT_MODELS);
      return (response.data?.providers ?? []).map((provider) => ({
        provider: provider.provider_name,
        defaultModel: provider.default_model ?? null,
        models: (provider.models ?? []).map((model) => model.model_name),
      }));
    },
  });
}
