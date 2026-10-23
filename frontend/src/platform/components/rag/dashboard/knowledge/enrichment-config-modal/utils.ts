import { filter, isEmpty, map, split, trim } from "lodash-es";

import type { EnrichmentFormData } from "./schemas";
import type { EnrichmentField } from "./types";

/**
 * Only for a row saved before the service returned `field_type_label` — every
 * field it answers with now carries one. Not a picker and not a vocabulary: the
 * label is free text on the wire, and there is no endpoint that lists the
 * possibilities, so this is a last resort rather than a source of truth.
 */
const LEGACY_TYPE_WORDS: Record<string, string> = {
  str: "Text",
  "list[str]": "Text, several values",
  int: "Number",
  float: "Number",
  bool: "Yes / no",
  dict: "Group of fields",
  "list[dict]": "Table",
};

/**
 * A field's type in words, for the screens that never show a raw `field_type`.
 *
 * Prefers the service's own `field_type_label`, which it returns on every field.
 */
export const typeInWords = (field?: {
  field_type?: string | null;
  field_type_label?: string | null;
}): string => {
  const label = field?.field_type_label?.trim();
  if (label) return label;
  const raw = (field?.field_type || "").trim().toLowerCase();
  return LEGACY_TYPE_WORDS[raw] || (raw ? field!.field_type! : "—");
};

export const toFormData = (fields: EnrichmentField[]): EnrichmentFormData => ({
  fields: map(fields, (field) => ({
    ...field,
    field_type_label: field.field_type_label ?? null,
    max_items: field.max_items ?? null,
    sample_values: isEmpty(field.sample_values)
      ? ""
      : field.sample_values.join("\n"),
    // Convert schema object/array to JSON string for form editing
    schema:
      field.schema !== null && field.schema !== undefined
        ? JSON.stringify(field.schema, null, 2)
        : null,
  })),
});

export const toPayload = (
  formData: EnrichmentFormData,
  /**
   * `omitFieldType` is for the screens where the type is the service's to
   * resolve — from the description at creation, from `field_type_label` on a
   * re-type. Sending today's `field_type` alongside a new label would leave the
   * service choosing between them, and it chooses `field_type`.
   */
  options?: { omitFieldType?: boolean },
): Array<
  Omit<
    EnrichmentField,
    "id" | "knowledge_id" | "created_at" | "updated_at" | "metadata"
  >
> =>
  map(
    formData.fields,
    ({ max_items, sample_values, id, schema, field_type, ...rest }) => {
      // Handle max_items conversion: string, number, null, or empty string -> null or positive number
      let processedMaxItems: number | null = null;
      if (max_items !== null && max_items !== undefined && max_items !== "") {
        const num =
          typeof max_items === "string" ? Number(max_items) : max_items;
        if (!Number.isNaN(num) && num > 0) {
          processedMaxItems = num;
        }
      }

      const typeLower = (field_type || "").toLowerCase();
      const allowSchema =
        typeLower === "dict" ||
        typeLower.includes("list[dict]") ||
        typeLower.includes("array[dict]");

      // Handle schema: only include for dict/list-of-dict; otherwise force null
      let processedSchema: any = null;
      if (allowSchema) {
        if (schema && typeof schema === "string" && trim(schema)) {
          try {
            processedSchema = JSON.parse(schema);
          } catch {
            // Invalid JSON - will be caught by form validation
            processedSchema = null;
          }
        } else if (schema !== null && schema !== undefined && schema !== "") {
          processedSchema = schema;
        }
      } else {
        processedSchema = null;
      }

      const payload: any = {
        ...rest,
        sample_values: filter(
          map(split(sample_values, /[\n,]/), (value) => trim(value)),
        ),
        max_items: processedMaxItems,
      };
      // A schema is only ever sent when there is one. Sending `schema: null`
      // alongside a plain field answers 200 with "Cannot set 'schema' for field
      // … Schema is only allowed for 'dict' and 'List[dict]' types" in
      // `summary.warnings` — which the screens show verbatim, so every ordinary
      // edit reported a problem it had not caused. (Live-checked 2026-09-16.)
      if (processedSchema !== null && processedSchema !== undefined) {
        payload.schema = processedSchema;
      }
      // An empty type is a screen that leaves the type to the service, which
      // resolves it from the description. Sending "" is a 422; sending a guess
      // would pin the wrong one.
      if (field_type && !options?.omitFieldType) {
        payload.field_type = field_type;
      }
      // Only include id if it's not 0 (new field) - for new fields, don't include id at all
      if (id !== 0 && id !== null && id !== undefined) {
        payload.id = id;
      }
      return payload;
    },
  );
