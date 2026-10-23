export type FieldLevel = "document" | "chunk";

export type EnrichmentField = {
  id: number;
  knowledge_id?: string;
  field_name: string;
  description: string;
  field_type: string;
  /**
   * The type in words — "Text", "Number", "Date", "Group of fields", "Table".
   *
   * The service returns it on every field and it is free text, not an enum, so
   * there is nothing to populate a dropdown from and no endpoint that lists the
   * possibilities. It is what a person is shown; `field_type` stays the wire
   * value the extractor validates against. (product review, 2026-09-01.)
   */
  field_type_label?: string | null;
  level: FieldLevel;
  is_custom: boolean;
  sample_values: string[];
  restrict_to_choices: boolean;
  max_items: number | null;
  is_active: boolean;
  is_required: boolean;
  schema?: any | null; // Can be any JSON-serializable value: object, array, string, number, boolean, null
  deprecated_at?: string | null;
  created_at?: string;
  updated_at?: string;
  metadata?: string[]; // Unique values from sample documents (when include_metadata=true)
};

export type EnrichmentConfigResponse = {
  fields: EnrichmentField[];
};

/**
 * How the service read a description when it resolved a field's type.
 *
 * Returned by `POST …/enrichment` — the type is always worked out from the
 * description, never from a label anyone sent — so this reasoning is the only
 * account of why a field ended up a Date rather than Text, and is shown rather
 * than dropped.
 */
export type TypeResolution = {
  field_name: string;
  field_type: string;
  field_type_label?: string | null;
  reason?: string | null;
};

/**
 * Who decides a field's type.
 *
 * - `explicit` — the picker this page has always had: seven wire types, chosen
 *   by hand, immutable afterwards.
 * - `resolved` — the service works the type out from the description and
 *   answers with its reasoning; the screen shows `field_type_label` and never a
 *   raw `field_type`, and a re-type is a new label on PATCH rather than a new
 *   type. This is what Document Intelligence uses.
 */
export type TypeMode = "explicit" | "resolved";

/**
 * What a save answers, beyond the row itself.
 *
 * `summary.warnings` is the service's own wording for something it declined to
 * do — most often a re-type refused because a document already holds a value
 * for that field. It is shown verbatim: paraphrasing it would be inventing a
 * reason the backend did not give.
 */
export type EnrichmentSaveResponse = {
  message?: string;
  type_resolutions?: TypeResolution[];
  summary?: { warnings?: string[] | null } | null;
};

export type FieldKey =
  | "field_name"
  | "description"
  | "field_type"
  | "field_type_label"
  | "level"
  | "sample_values"
  | "restrict_to_choices"
  | "max_items"
  | "is_required"
  | "is_active"
  | "is_custom"
  | "schema";

export interface EnrichmentConfigModalProps {
  open: boolean;
  knowledgeId: string | null;
  onClose: () => void;
  documentMetadataEnabled?: boolean;
  chunkMetadataEnabled?: boolean;
  metadataLoading?: boolean;
}

export type FieldToDelete = {
  id: number;
  name: string;
  index: number;
};
