import type { NewField } from "@/data/createCollection";
import type { CatalogField } from "@/data/fieldCatalog";
import type { PreviewField, SuggestedField } from "@/data/schemaDraft";

import { TEXT_TYPE } from "./constants";

/**
 * The field list a collection is about to be created with, while it is still
 * being argued with.
 *
 * Every draft on this board came from the service — a template's published
 * fields or the schema call's proposals — and the list is sent back verbatim
 * when the collection is created. That round trip is the point: what someone
 * confirms here is what gets built, rather than a picture of a decision made
 * somewhere else. Nothing in this file guesses a type or invents a field.
 *
 * The service's *optional default* fields are deliberately absent. They are
 * neither shown nor sent: the config PUT already goes out with
 * `seed_optional_defaults: false`, so a collection is created with exactly the
 * list on this board plus the two protected fields, and nothing arrives that
 * nobody asked for.
 *
 * Everything here is pure so it can be tested without a collection, a session
 * or a network.
 */

/** Where a draft came from, which is what the board says about each row. */
export type FieldOrigin =
  /** Published by the template the person picked. */
  | "template"
  /** Proposed by the schema call from the wording, or the sample document. */
  | "proposed"
  /** Found on the sample document by the preview call, and accepted. */
  | "suggested"
  /** Typed by hand on this board. */
  | "custom";

export interface FieldDraft {
  /** Stable across re-renders so a row keeps focus while it is typed in. */
  id: string;
  /** Lowercase, underscored — what the field will be called. */
  name: string;
  /**
   * What it should capture, in the person's own words or the service's.
   *
   * Never blank on a field that gets created: the route refuses a new field
   * with no description, and refuses the whole batch with it.
   */
  desc: string;
  /**
   * The wire type — `str`, `int`, `List[dict]`. Empty means "auto": the field
   * is sent with no type and the service resolves one from the description.
   */
  fieldType: string;
  /**
   * The columns of a `dict`/`List[dict]`, `{ column_name: type }`. Only ever
   * set on a type whose catalogue entry says `requires_schema`.
   */
  schema?: Record<string, string>;
  /**
   * `"document"` or `"chunk"`, where the service said which.
   *
   * Carried rather than defaulted: a chunk-level field created with no `level`
   * lands at document level and nothing says so.
   */
  level?: string;
  origin: FieldOrigin;
  /** The service's own reason, where it gave one. Shown, never invented. */
  why?: string;
}

let sequence = 0;
/** Ids only have to be unique within one unsaved form. */
function nextId(): string {
  sequence += 1;
  return `f${sequence}`;
}

/**
 * A field name is lowercase with underscores, and the service refuses anything
 * else.
 *
 * `typing` keeps a trailing underscore while the reader is mid-word, so
 * `total_` can become `total_amount`; the trailing underscore is trimmed only
 * once they have finished. Normalising on blur alone was the other option and
 * it silently rewrites what someone typed after they have looked away.
 */
export function slugify(raw: string, typing = false): string {
  const out = String(raw)
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/_{2,}/g, "_")
    .replace(/^_+/, "");
  return typing ? out : out.replace(/_+$/, "");
}

/**
 * One catalogue field — a template's, or one the schema call proposed.
 *
 * A field the service described arrives with its description; one it did not
 * gets a placeholder built from its name rather than an empty box. The route
 * refuses a new field with no description, so a blank here would be a row that
 * blocks the step through no fault of the person reading it — and the only
 * blank descriptions that should stop anybody are the ones they left blank
 * themselves.
 */
export function draftFromCatalog(
  field: CatalogField,
  origin: FieldOrigin,
): FieldDraft {
  const name = slugify(field.field_name);
  return {
    id: nextId(),
    name,
    desc: field.description?.trim() || describedFrom(name),
    // A field the service typed keeps its type; one it left untyped is text,
    // because the board no longer offers "work it out" as a row's answer and a
    // row with no type would read as whichever option happened to be first.
    fieldType: field.field_type || TEXT_TYPE,
    ...(field.schema ? { schema: { ...field.schema } } : {}),
    ...(field.level ? { level: field.level } : {}),
    origin,
  };
}

export function draftsFromCatalog(
  fields: CatalogField[],
  origin: FieldOrigin,
): FieldDraft[] {
  return fields.map((field) => draftFromCatalog(field, origin));
}

/**
 * A stand-in description, from the field's own name.
 *
 * Not clever, and not meant to be: it exists so a field the service named but
 * did not describe can still be created, since the route refuses one with no
 * description. It is in the box, visible and editable, rather than filled in
 * behind anybody's back.
 */
function describedFrom(name: string): string {
  const words = name.replace(/_/g, " ").trim();
  return words ? `The ${words} on each document.` : "";
}

/** A field the preview call found on the document, accepted onto the board. */
export function draftFromSuggestion(field: SuggestedField): FieldDraft {
  return {
    id: nextId(),
    name: slugify(field.field_name),
    desc: field.description?.trim() || describedFrom(slugify(field.field_name)),
    fieldType: field.field_type || TEXT_TYPE,
    origin: "suggested",
    why: field.reason ?? undefined,
  };
}

/**
 * Adds drafts to a board without creating a duplicate name.
 *
 * The schema call can, rarely, propose a field a previously picked template
 * already published, and the backend does not reject a duplicate `field_name` —
 * it would simply create the same field twice. An existing row wins: it may
 * have been edited already, and silently overwriting an edit with a fresh
 * proposal is worse than ignoring the proposal.
 */
export function mergeDrafts(
  existing: FieldDraft[],
  incoming: FieldDraft[],
): FieldDraft[] {
  const taken = new Set(existing.map((draft) => slugify(draft.name)));
  const added = incoming.filter((draft) => {
    const name = slugify(draft.name);
    if (!name || taken.has(name)) return false;
    taken.add(name);
    return true;
  });
  return [...existing, ...added];
}

/**
 * A field added by hand, ready to be renamed.
 *
 * It starts as text rather than as "work it out": text is what almost every
 * hand-added field turns out to be, it is the one type that cannot be wrong
 * enough to matter — everything reads back as what the document said — and a
 * row that arrives already typed is a row with one thing left to do. The type
 * is the catalogue's own text entry where the caller has one, so this never
 * hardcodes a wire value the service has renamed.
 */
export function blankDraft(
  existing: FieldDraft[],
  fieldType: string = TEXT_TYPE,
): FieldDraft {
  const taken = new Set(existing.map((draft) => slugify(draft.name)));
  let name = "new_field";
  let suffix = 2;
  while (taken.has(name)) {
    name = `new_field_${suffix}`;
    suffix += 1;
  }
  return {
    id: nextId(),
    name,
    desc: "",
    fieldType,
    origin: "custom",
  };
}

/**
 * A row nobody has finished saying anything about.
 *
 * Adding a field puts an empty row on the board, and an empty row is a
 * question, not a mistake: it is left out of what gets created and out of what
 * a sample run is read with, so it can sit there half-written without holding
 * the step up. Every other problem on the board is a real conflict between
 * fields somebody did write, and those still have to be fixed.
 */
export function isUnfinished(draft: FieldDraft): boolean {
  return !slugify(draft.name) || !draft.desc.trim();
}

/**
 * The ways this board can produce something the service will refuse.
 *
 * The backend rejects none of these — it does not check for a blank or
 * duplicate `field_name`, and it will take a table with no columns — so every
 * one of them has to be caught here. Keyed by draft id so a message survives a
 * reorder.
 *
 * Two kinds of message come back together, because both belong on the row they
 * are about: an unfinished row says what is missing and that it is left out
 * until then (`isUnfinished` tells them apart), and everything else says what
 * has to be fixed before the step will continue.
 */
export function validateDrafts(
  drafts: FieldDraft[],
  requiresSchema: (fieldType: string) => boolean = () => false,
): Record<string, string> {
  const problems: Record<string, string> = {};
  const seen = new Map<string, string>();

  for (const draft of drafts) {
    const name = slugify(draft.name);
    if (!name) {
      problems[draft.id] = "Name this field, or it is left out.";
      continue;
    }
    const first = seen.get(name);
    if (first) {
      const message = `Another field is already called ${name}.`;
      problems[draft.id] = message;
      problems[first] = message;
      continue;
    }
    seen.set(name, draft.id);

    if (!draft.desc.trim()) {
      // The route refuses a new field with no description — and refuses the
      // entire batch with it, so one blank box would be a create that produces
      // a collection with nothing on it. Left out rather than sent, which is
      // also why this does not stop the step.
      problems[draft.id] =
        "Say what this field should capture, or it is left out.";
      continue;
    }

    if (requiresSchema(draft.fieldType)) {
      const columns = Object.keys(draft.schema ?? {});
      if (!columns.length) {
        problems[draft.id] =
          "This type is made of sub-fields — add at least one.";
      } else if (columns.some((column) => !slugify(column))) {
        problems[draft.id] = "Every sub-field needs a name.";
      }
    }
  }

  return problems;
}

/**
 * The board, as the collection is told to create it.
 *
 * Unfinished rows are not in it: a field with no name or no description is
 * something somebody started, not something to create, and the create route
 * refuses the whole batch over one of them.
 */
export function toNewFields(drafts: FieldDraft[]): NewField[] {
  return drafts
    .filter((draft) => !isUnfinished(draft))
    .map((draft) => ({
      field_name: slugify(draft.name),
      description: draft.desc.trim(),
      ...(draft.fieldType ? { field_type: draft.fieldType } : {}),
      ...(draft.level ? { level: draft.level } : {}),
      ...(draft.schema && Object.keys(draft.schema).length
        ? { schema: draft.schema }
        : {}),
    }));
}

/** The board, as the preview call wants to be told about it. */
export function toPreviewFields(drafts: FieldDraft[]): PreviewField[] {
  return toNewFields(drafts).map((field) => ({
    field_name: field.field_name,
    ...(field.description ? { description: field.description } : {}),
    ...(field.field_type ? { field_type: field.field_type } : {}),
    ...(field.schema ? { schema: field.schema } : {}),
  }));
}

/**
 * What the wording box is worth sending as `extraction_instructions`.
 *
 * The board is no longer folded back into prose — it is sent as fields now —
 * so this is only the person's own words, trimmed. Kept as a function because
 * the schema call treats an empty string and an absent field differently.
 */
export function instructionsFrom(wanted: string): string | undefined {
  const trimmed = String(wanted ?? "").trim();
  return trimmed || undefined;
}
