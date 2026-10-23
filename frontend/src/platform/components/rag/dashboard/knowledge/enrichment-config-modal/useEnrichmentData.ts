"use client";

import { useQuery } from "@tanstack/react-query";
import { map } from "lodash-es";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./constants";
import type { EnrichmentConfigResponse } from "./types";

export const useEnrichmentData = (
  knowledgeId: string | null,
  open: boolean,
) => {
  return useQuery({
    queryKey: ["knowledge-enrichment-config", knowledgeId],
    queryFn: async () => {
      if (!knowledgeId) {
        throw new Error("Knowledge ID missing");
      }
      const url = buildApiUrl(APIS.GET_ENRICHMENT_CONFIG, {
        knowledge_id: knowledgeId,
      });
      const response = await axios.get<EnrichmentConfigResponse>(url);
      const formattedData = map(response.data.fields, (field) => ({
        ...field,
        max_items: field.max_items ?? null,
      }));
      return {
        ...response.data,
        fields: formattedData,
      };
    },
    enabled: Boolean(open && knowledgeId),
    staleTime: 0,
  });
};
