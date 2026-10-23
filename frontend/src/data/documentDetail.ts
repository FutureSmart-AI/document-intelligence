"use client";

import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/platform/hooks/useAuth";
import { buildApiUrl } from "@/platform/utils";
import axios, { getDocumentChunks } from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * What one document's own screen needs, beyond the row the table already has.
 *
 * Two things: the text the extraction actually read — which is what makes
 * "check this value against the document" possible without downloading
 * anything — and which of the collection's fields are required, which is the
 * only way an empty one can say whether it matters.
 *
 * Both read the collection from the same auth context every other request in
 * this product reads it from, so a screen never has to thread the collection id
 * through three components to ask for a document.
 */

/** One passage of the document, as the service stored it. */
export interface DocumentSection {
  id: string;
  text: string;
}

/**
 * The extracted text, in the passages it was split into.
 *
 * The same route the agent UI's own detail panel reads, so this is not a second
 * source of truth for the same words — it is the same one, rendered as a
 * document rather than as a list of chunk cards.
 */
export function useDocumentSections(objectId: string | null) {
  const { knowledgeId } = useAuth();
  return useQuery<DocumentSection[]>({
    queryKey: ["di-document-sections", knowledgeId, objectId],
    enabled: Boolean(knowledgeId && objectId),
    retry: false,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const response = await getDocumentChunks({
        object_id: objectId as string,
        knowledgeId: knowledgeId as string,
      });
      return (response?.chunks ?? [])
        .map((chunk) => ({ id: chunk.id, text: String(chunk.text ?? "") }))
        .filter((chunk) => chunk.text.trim().length > 0);
    },
  });
}

/** One field of the collection's schema, as the service reports it. */
export interface EnrichmentField {
  name: string;
  /** The type in words — `field_type_label`, never a raw `field_type`. */
  typeLabel: string;
  required: boolean;
  description: string;
}

function typeInWords(field: {
  field_type?: string | null;
  field_type_label?: string | null;
}): string {
  const label = field.field_type_label?.trim();
  if (label) return label;
  return field.field_type?.trim() || "—";
}

/**
 * The collection's fields, so a document's own list can say which of them the
 * collection insists on.
 *
 * Required and optional are drawn apart because an empty *required* field is a
 * document worth looking at and an empty optional one usually is not. Without
 * this the list can only say "Not found" on both and leave the reader to work
 * out which of those matter.
 */
export function useEnrichmentFields() {
  const { knowledgeId } = useAuth();
  return useQuery<EnrichmentField[]>({
    queryKey: ["di-enrichment-fields", knowledgeId],
    enabled: Boolean(knowledgeId),
    retry: false,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const response = await axios.get(
        buildApiUrl(APIS.ENRICHMENT, { knowledge_id: knowledgeId as string }),
      );
      const fields: Array<Record<string, unknown>> = Array.isArray(
        response.data?.fields,
      )
        ? response.data.fields
        : [];
      return fields.map((field) => ({
        name: String(field.field_name ?? ""),
        typeLabel: typeInWords(
          field as { field_type?: string; field_type_label?: string },
        ),
        required: Boolean(field.is_required),
        description: String(field.description ?? ""),
      }));
    },
  });
}
