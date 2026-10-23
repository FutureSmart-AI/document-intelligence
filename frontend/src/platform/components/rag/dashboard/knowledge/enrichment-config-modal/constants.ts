import type { FieldKey } from "./types";

// Protected system fields that cannot be deleted by users
export const PROTECTED_SYSTEM_FIELDS = new Set([
  "summary",
  "chunk_idx",
  "chunk_text",
  "document_type",
]);

export const isProtectedField = (
  fieldName: string | undefined | null,
): boolean => !!fieldName && PROTECTED_SYSTEM_FIELDS.has(fieldName);

export const APIS = {
  GET_ENRICHMENT_CONFIG: "/api/knowledge/v1/{knowledge_id}/enrichment",
  /**
   * Creating fields. PATCH creates a field sent without an id too, but only
   * POST answers with `type_resolutions` — the reasoning behind the type it
   * worked out from the description — so a screen that shows that reasoning has
   * to use this one.
   */
  POST_ENRICHMENT_CONFIG: "/api/knowledge/v1/{knowledge_id}/enrichment",
  PATCH_ENRICHMENT_CONFIG: "/api/knowledge/v1/{knowledge_id}/enrichment",
  DELETE_ENRICHMENT_FIELDS:
    "/api/knowledge/v1/{knowledge_id}/enrichment/fields",
} as const;

export const FIELD_META: Record<
  FieldKey,
  { label: string; tooltip: string; helper?: string; required?: boolean }
> = {
  field_name: {
    label: "Field name",
    tooltip:
      "Unique identifier for this metadata field. Cannot be changed after creation because downstream filters and indices depend on it.",
    helper:
      "Example: skills, degree, experience_summary. Immutable after creation.",
    required: true,
  },
  description: {
    label: "Description",
    tooltip:
      "Explain what the field should capture so the LLM can extract it. For composite fields (like experience, education, etc.), instruct the LLM to break them into structured sub-fields as a nested dictionary.",
    helper:
      "Helps prompt design and future collaborators. For composite fields, add: 'Return as a nested dictionary with structured sub-fields (e.g., experience: {company, role, duration, location})'.",
    required: true,
  },
  field_type: {
    label: "Field type",
    tooltip:
      "Data type expected for this field. Examples: str, List[str], int, float, bool. Cannot be changed after creation.",
    helper: "Used to validate LLM output. Immutable after creation.",
    required: true,
  },
  field_type_label: {
    label: "Type",
    tooltip:
      "The type in words — Text, Number, Date, Group of fields, Table. Free text, so write whatever describes the field. The type itself is worked out from the description, never from this label, so changing it here only renames what you see — and only while no document has a value for this field yet.",
    helper:
      "Renaming the type takes effect only while no document has a value for this field.",
  },
  level: {
    label: "Level",
    tooltip:
      "Document level extracts once per document. Chunk level extracts per chunk. Cannot be changed after creation.",
    helper:
      "Enable the corresponding metadata level in Knowledge Config. Immutable after creation.",
    required: true,
  },
  sample_values: {
    label: "Sample values",
    tooltip:
      "Example outputs to guide the LLM. Separate values with commas or line breaks.",
    helper: "Leave blank if free-form. Required when restricting choices.",
  },
  restrict_to_choices: {
    label: "Restrict to sample values",
    tooltip:
      "When enabled, the extractor must pick from the provided sample values.",
    helper: "Great for controlled vocabularies like degree types.",
  },
  max_items: {
    label: "Max items",
    tooltip:
      "Maximum number of values allowed for list-type fields. Leave empty (null) for no limit.",
    helper: "Leave empty for no limit. Optional for list fields.",
  },
  is_required: {
    label: "Required field",
    tooltip:
      "If true, extraction will be considered incomplete when this field is missing.",
  },
  is_active: {
    label: "Active",
    tooltip:
      "Toggle to disable the field without deleting historical data. Inactive fields are ignored during extraction. You can turn it on later if needed.",
  },
  is_custom: {
    label: "Custom field",
    tooltip:
      "Whether this field is user-defined or system-provided. All fields can be edited and deleted except protected system fields (summary, chunk_idx, chunk_text, document_type).",
  },
  schema: {
    label: "Schema",
    tooltip:
      "JSON schema definition for this field. Can be any JSON-serializable value: object, array, string, number, boolean, or null.",
    helper:
      'Optional JSON schema definition. Must be valid JSON. Example: {"role": "str", "company": "str"}',
  },
};

export const fieldTypeOptions = [
  { label: "String (str)", value: "str" },
  { label: "List of Strings (List[str])", value: "List[str]" },
  { label: "Integer (int)", value: "int" },
  { label: "Float (float)", value: "float" },
  { label: "Boolean (bool)", value: "bool" },
  { label: "Dictionary/Object (dict)", value: "dict" },
  { label: "List of Dictionaries (List[dict])", value: "List[dict]" },
];

export const getLevelOptions = (
  documentMetadataEnabled: boolean,
  chunkMetadataEnabled: boolean,
) => [
  {
    label: "Document",
    value: "document",
    disabled: !documentMetadataEnabled,
  },
  {
    label: "Chunk",
    value: "chunk",
    disabled: !chunkMetadataEnabled,
  },
];

export const SCHEMA_VALUE_TYPE_OPTIONS = [
  { label: "String (str)", value: "str" },
  { label: "List of Strings (List[str])", value: "List[str]" },
];
