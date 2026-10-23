"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";
import { useOwnerId } from "./session";

/** One field, exactly as the collection is told to create it. */
export interface NewField {
  field_name: string;
  /**
   * Required, and required to be non-empty.
   *
   * The route rejects a new field with no description — and rejects the whole
   * call with it, applying none of the other fields ("Validation failed on one
   * or more fields. No changes were applied."). Live-checked 2026-09-14: both
   * an omitted `description` and `""` answer `400`. So this is not optional on
   * the type either, which is what makes a blank one a compile error rather
   * than a create that silently produces a collection with two fields.
   */
  description: string;
  /**
   * The wire type — `str`, `int`, `List[dict]`. Optional: leaving it off is
   * the "auto" case, where the service resolves one from the description.
   */
  field_type?: string;
  /**
   * `"document"` or `"chunk"`. Sent for the same reason it is carried
   * everywhere else: a chunk-level field created without it lands at document
   * level, silently.
   */
  level?: string;
  /** The columns of a `dict`/`List[dict]`, `{ column_name: type }`. */
  schema?: Record<string, string>;
}

export interface NewCollection {
  name: string;
  /** "What are these documents about?" — the collection's context sentence. */
  about: string;
  /**
   * The one fork in the product: on, the named fields are extracted from every
   * document and Data shows a table; off, documents are searchable and
   * answerable only.
   */
  structuredExtraction: boolean;
  /**
   * The confirmed field list, as the person left it — template fields they
   * kept, fields the service proposed, optional defaults they did not drop,
   * and anything they typed themselves.
   *
   * This is the whole list and it is written in one call. Nothing derives it
   * server-side any more: what was on the board is what gets built.
   */
  fields: NewField[];
}

export interface CreatedCollection {
  /** The knowledge id — this product's collection id. */
  collectionId: string;
  knowledgeId: string;
  /** The field names that were written, in the order they were sent. */
  createdFields: string[];
  /**
   * Set when the collection was created but its fields were not.
   *
   * Worth saying out loud rather than swallowing: the collection stands and is
   * usable, but it carries only the two protected defaults, so the next
   * document is read against a schema nobody asked for.
   */
  fieldsFailed: boolean;
}

/** The steps the create screen reports while it works. */
export type CreateStep = "collection" | "fields" | "documents";

/**
 * Creates a collection: one knowledge base, configured, and its fields.
 *
 * Three calls, in this order, and the order is load-bearing.
 *
 * 1. `POST /v1` mints the knowledge base but leaves its config uninitialized —
 *    `GET …/config` answers "Knowledge config exists but is not initialized",
 *    and an upload answers "Embedding model not found", until something has
 *    been `PUT`.
 * 2. `PUT …/config` initializes it and adopts it into this product: the board
 *    lists the knowledge bases carrying a `collection_name`.
 * 3. `POST …/enrichment` creates the fields — one call for the entire list.
 *
 * ## The two traps this call exists to avoid
 *
 * **`seed_optional_defaults: false` goes on every config PUT.** Without it the
 * config call force-creates all nine document-level defaults (`title`,
 * `summary`, `document_type`, `topics`, `keywords`, `entities`,
 * `organizations`, `temporal_context`, `document_outline`) whatever the person
 * actually asked for — the resume template publishes 13 fields and the
 * collection came back holding 22. With the flag, the config call creates only
 * the two still-protected fields (`summary`, `document_type`) and everything
 * else is written once, deliberately, by step 3.
 *
 * **`is_custom: true` goes on every field.** The route requires it — omitting
 * it is a `422` naming `body.fields.0.is_custom` — and there is no "system
 * field" concept from this screen: a template's field, a proposed field and a
 * hand-typed one are all created the same way.
 *
 * `field_template` is deliberately **not** sent on the config PUT. It is never
 * stored, and with `seed_optional_defaults: false` it seeds nothing either; the
 * template's fields reached the browser from `GET /v1/field-templates` and are
 * already on the list step 3 writes.
 *
 * `PUT …/chat/config` is deliberately not called either. Chat works with no
 * chat config at all, and its unconfigured behaviour is already what this
 * product promises — sources shown, follow-up questions on, answers from the
 * service's own default model. Settings is where any of that gets changed.
 */
export function useCreateCollection(options?: {
  onStep?: (step: CreateStep) => void;
}) {
  const ownerId = useOwnerId();
  const client = useQueryClient();

  return useMutation<CreatedCollection, unknown, NewCollection>({
    mutationFn: async (input) => {
      if (!ownerId) throw new Error("Not signed in");
      options?.onStep?.("collection");

      // 1. The knowledge base that holds the documents.
      const created = await axios.post<{ knowledge_id: string }>(
        APIS.KNOWLEDGE,
        { owner_id: ownerId, collection_name: input.name },
      );
      const knowledgeId = created.data?.knowledge_id;
      if (!knowledgeId) throw new Error("Collection was not created");

      // 2. Initialise it — with what the person typed, and nothing else.
      //
      //    The embedding model, hybrid search, the metadata levels and the
      //    extraction LLM are all left out on purpose: the service fills in its
      //    own defaults, so hardcoding today's values here would freeze them
      //    into a released UI and stop the backend changing them. There is no
      //    picker for any of them on this screen.
      //
      //    The one exception is a person turning structured extraction off,
      //    which is not the default and so has to be said. `enrichment_enabled`
      //    is what says it: the service derives both metadata levels from it.
      const config: Record<string, unknown> = {
        collection_name: input.name,
        service_description: input.about,
        seed_optional_defaults: false,
      };
      if (!input.structuredExtraction) {
        config.enrichment_enabled = false;
      }
      await axios.put(
        buildApiUrl(APIS.KNOWLEDGE_CONFIG, { knowledge_id: knowledgeId }),
        config,
      );

      // 3. The fields, exactly as they were confirmed.
      //
      //    One call for the whole list, and the service applies it all or none
      //    of it: a single field it refuses rejects the batch. That is why the
      //    board validates names, descriptions and table columns before anybody
      //    reaches this step.
      const fields = input.structuredExtraction ? input.fields : [];
      let fieldsFailed = false;
      if (fields.length) {
        options?.onStep?.("fields");
        try {
          await axios.post(
            buildApiUrl(APIS.ENRICHMENT, { knowledge_id: knowledgeId }),
            {
              fields: fields.map((field) => ({
                field_name: field.field_name,
                // Always sent, never blank — see `NewField.description`.
                description: field.description,
                // No `field_type` means "auto": the service resolves one from
                // the description, which is the documented fallback and not a
                // failure to send something.
                ...(field.field_type ? { field_type: field.field_type } : {}),
                ...(field.level ? { level: field.level } : {}),
                ...(field.schema ? { schema: field.schema } : {}),
                is_custom: true,
              })),
            },
          );
        } catch {
          // The collection exists and carries its two protected fields. Losing
          // the collection over a schema that can be fixed in Settings would be
          // worse than saying what happened.
          fieldsFailed = true;
        }
      }

      options?.onStep?.("documents");

      return {
        collectionId: knowledgeId,
        knowledgeId,
        createdFields: fieldsFailed ? [] : fields.map((f) => f.field_name),
        fieldsFailed,
      };
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["di-collections"] });
    },
  });
}
