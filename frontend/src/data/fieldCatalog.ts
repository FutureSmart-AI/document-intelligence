"use client";

import { useQuery } from "@tanstack/react-query";

import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * The two catalogues the create screen is built from: the kinds of document it
 * can start you off with, and the types a field can be read as.
 *
 * Both are read from the service rather than shipped as constants, and that is
 * the point of this file rather than an incidental detail. The screen used to
 * hardcode five templates and eight type labels; both lists are the service's
 * to change, and a copy of them in the frontend is a copy that goes wrong
 * quietly — a type label drifting out of step with what Settings prints, or a
 * template the backend added that nobody can pick.
 */

/** One field as a template or the schema call describes it. */
export interface CatalogField {
  field_name: string;
  description?: string | null;
  /** The wire value — `str`, `List[dict]`, and so on. */
  field_type?: string | null;
  /** The same type in words, which is what any screen shows. */
  field_type_label?: string | null;
  /**
   * `"document"` or `"chunk"` — whether the field is pulled out once per
   * document or once per chunk of one.
   *
   * Carried rather than assumed: a chunk-level field created without it lands
   * at document level, silently (live-checked 2026-09-14 — `chunk_topic` sent
   * with no `level` came back `level: "document"`).
   */
  level?: string | null;
  /**
   * The columns of a `dict`/`List[dict]`, as `{ column_name: type }`.
   *
   * A column's own type is one of six — `str`, `int`, `float`, `bool`,
   * `List[str]`, `date`. There is no second level: a table's columns cannot
   * themselves be tables.
   */
  schema?: Record<string, string> | null;
}

/** One starting point — a kind of document, with the fields it usually has. */
export interface FieldTemplate {
  key: string;
  display_name: string;
  description?: string | null;
  /**
   * The wording the picker itself fills the create form in with.
   *
   * These are the template's *answers*, not its labels: `display_name` names
   * the card you click ("Resumes / CVs"), `default_collection_name` is what the
   * collection should be called ("Candidate Resumes"), and the other two are
   * the prose the schema call is actually given. They are published precisely
   * so nobody has to invent starting wording in the browser — a template whose
   * boxes come back empty is a template that reads as a dead card.
   */
  default_collection_name?: string | null;
  default_service_description?: string | null;
  default_extraction_instructions?: string | null;
  fields: CatalogField[];
}

/** One type a field can be read as. */
export interface FieldType {
  field_type: string;
  field_type_label: string;
  /** True for exactly the types that open a sub-field builder. */
  requires_schema: boolean;
}

/**
 * The types a **column inside a table** may be, which is a smaller set than the
 * types a top-level field may be.
 *
 * `dict` and `List[dict]` are top-level only — the service does not nest a
 * table inside a table — so the column builder filters the catalogue down to
 * the types that take no schema of their own rather than offering a choice
 * that cannot be sent.
 */
export const columnTypes = (types: FieldType[]): FieldType[] =>
  types.filter((entry) => !entry.requires_schema);

/** Words that tell apart wire types the service publishes under one label. */
const LABEL_QUALIFIERS: Record<string, string> = {
  int: "whole",
  float: "decimal",
};

/**
 * The catalogue with a distinct label per entry, for anything that renders a
 * dropdown.
 *
 * The service publishes `int` and `float` under one label ("Number"). Folding
 * them into one entry meant picking Number always sent `int`, and an `int`
 * field keeps whole numbers only — a per-mile rate of 0.18 came back as 0. So
 * every entry stays, and the ones sharing a label are told apart in words
 * ("Number (whole)", "Number (decimal)"), or by their wire value when there are
 * no words for it. Display only: new fields are sent as a `field_type`, never
 * as this label.
 */
export const distinctTypes = (types: FieldType[]): FieldType[] => {
  const perLabel = new Map<string, number>();
  for (const entry of types)
    perLabel.set(
      entry.field_type_label,
      (perLabel.get(entry.field_type_label) ?? 0) + 1,
    );
  return types.map((entry) =>
    (perLabel.get(entry.field_type_label) ?? 0) > 1
      ? {
          ...entry,
          field_type_label: `${entry.field_type_label} (${
            LABEL_QUALIFIERS[entry.field_type] ?? entry.field_type
          })`,
        }
      : entry,
  );
};

/**
 * The label to print for a wire type.
 *
 * Falls back to the wire value, which is ugly but true — inventing a friendly
 * word for a type the catalogue has not described is how the last hardcoded
 * label map went stale.
 */
export const labelFor = (types: FieldType[], fieldType: string): string =>
  types.find((entry) => entry.field_type === fieldType)?.field_type_label ??
  fieldType;

/** Whether picking this type should open the sub-field builder. */
export const needsSchema = (types: FieldType[], fieldType: string): boolean =>
  types.find((entry) => entry.field_type === fieldType)?.requires_schema ??
  // Unknown to the catalogue: assume the simple case rather than opening a
  // builder for a type that may not take one.
  false;

const asField = (raw: unknown): CatalogField => {
  const entry = (raw ?? {}) as CatalogField;
  return {
    field_name: String(entry.field_name ?? ""),
    description: entry.description ?? null,
    field_type: entry.field_type ?? null,
    field_type_label: entry.field_type_label ?? null,
    level: entry.level ?? null,
    schema:
      entry.schema && typeof entry.schema === "object" ? entry.schema : null,
  };
};

/**
 * The template picker's contents.
 *
 * Cached for the session: it is a catalogue, it does not change while someone
 * fills in a form, and the create screen reads it on mount where a spinner
 * would be the first thing anybody sees.
 */
export function useFieldTemplates() {
  return useQuery({
    queryKey: ["di-field-templates"],
    staleTime: 60 * 60 * 1000,
    queryFn: async (): Promise<FieldTemplate[]> => {
      const { data } = await axios.get(APIS.FIELD_TEMPLATES);
      const list = Array.isArray(data?.templates) ? data.templates : [];
      const templates: FieldTemplate[] = list.map(
        (raw: Record<string, unknown>) => ({
          key: String(raw?.key ?? ""),
          display_name: String(raw?.display_name ?? raw?.key ?? ""),
          description: (raw?.description as string) ?? null,
          default_collection_name:
            (raw?.default_collection_name as string) ?? null,
          default_service_description:
            (raw?.default_service_description as string) ?? null,
          default_extraction_instructions:
            (raw?.default_extraction_instructions as string) ?? null,
          fields: Array.isArray(raw?.fields) ? raw.fields.map(asField) : [],
        }),
      );

      // The service sends the preset-less kind first; it reads as the last
      // resort, so it goes last. Everything else keeps the order published.
      return [
        ...templates.filter((entry) => !isOwnWords(entry)),
        ...templates.filter(isOwnWords),
      ];
    },
  });
}

/**
 * "Something else" — the kind that is not a kind.
 *
 * Matched on having no fields rather than on the key alone, so a preset-less
 * entry the service names differently still sorts to the end.
 */
const isOwnWords = (template: FieldTemplate): boolean =>
  template.key === "default" || template.fields.length === 0;

/**
 * The type dropdown's contents.
 *
 * `int` and `float` come back as two entries under one label ("Number"); how a
 * dropdown tells them apart is a UI decision, made by `distinctTypes` where the
 * dropdown is built — this stays the catalogue as published.
 */
export function useFieldTypes() {
  return useQuery({
    queryKey: ["di-field-types"],
    staleTime: 60 * 60 * 1000,
    queryFn: async (): Promise<FieldType[]> => {
      const { data } = await axios.get(APIS.FIELD_TYPES);
      const list = Array.isArray(data?.field_types) ? data.field_types : [];
      return list
        .map((raw: Record<string, unknown>) => ({
          field_type: String(raw?.field_type ?? ""),
          field_type_label: String(
            raw?.field_type_label ?? raw?.field_type ?? "",
          ),
          requires_schema: Boolean(raw?.requires_schema),
        }))
        .filter((entry: FieldType) => entry.field_type);
    },
  });
}
