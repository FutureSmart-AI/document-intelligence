"use client";

import { browserStorageUrl } from "@/lib/storageUrl";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";
import type { CatalogField } from "./fieldCatalog";

/**
 * Working out a collection's fields *before* the collection exists.
 *
 * Both calls here write nothing — each answers with `run.persisted: false` —
 * and that is what lets the create screen show someone the schema, let them
 * argue with it, and run a real document against it, all before anything is
 * created. The fields are written once, at the end, by
 * `POST …/{knowledge_id}/enrichment`.
 *
 * Both are session-only: an API key, including the master key, is refused with
 * `401`, because there is no collection yet for a key to be scoped to.
 */

/** A field the service proposed and then threw out, and why. */
export interface RejectedField {
  field_name?: string | null;
  reason?: string | null;
}

/**
 * What `POST /v1/enrichment/generate` answers, as this screen reads it.
 *
 * The route also answers `default_field_verdicts` — its keep/drop opinion on
 * each of the ten optional default fields — and that is deliberately dropped
 * here. Those fields are not offered on the create screen and are not created
 * with the collection: the config PUT carries `seed_optional_defaults: false`,
 * so the only fields a new collection gets are the confirmed list plus the two
 * protected ones (`summary`, `document_type`).
 */
export interface GeneratedSchema {
  proposed_fields: CatalogField[];
  rejected_fields: RejectedField[];
}

/** What the schema call is asked. */
export interface GenerateInput {
  collectionName?: string;
  serviceDescription: string;
  extractionInstructions?: string;
  /** A `download_presigned_url` from `uploadSampleForSchema`. At most one. */
  fileUrl?: string;
}

const asRejected = (value: unknown): RejectedField[] =>
  Array.isArray(value)
    ? value.map((entry) =>
        typeof entry === "string"
          ? { field_name: entry }
          : {
              field_name: (entry as RejectedField)?.field_name ?? null,
              reason: (entry as RejectedField)?.reason ?? null,
            },
      )
    : [];

const asFields = (value: unknown): CatalogField[] =>
  Array.isArray(value)
    ? value
        .map((raw) => raw as CatalogField)
        .filter((entry) => entry?.field_name)
        .map((entry) => ({
          field_name: String(entry.field_name),
          description: entry.description ?? null,
          field_type: entry.field_type ?? null,
          field_type_label: entry.field_type_label ?? null,
          level: entry.level ?? null,
          schema:
            entry.schema && typeof entry.schema === "object"
              ? entry.schema
              : null,
        }))
    : [];

/**
 * Puts one sample document somewhere the schema call can read it.
 *
 * Three steps, because the schema call takes a URL rather than an upload: ask
 * for a pair of presigned URLs, `PUT` the bytes at the first, hand the second
 * back. The `Content-Type` on the `PUT` has to match what was asked for exactly
 * or the signature does not verify.
 *
 * The `PUT` goes out with `fetch`, not this app's axios: the presigned URL is
 * S3's, the signature covers the headers, and an interceptor adding a session
 * cookie or a JSON content type to it would break it.
 */
export async function uploadSampleForSchema(file: File): Promise<string> {
  const contentType = file.type || "application/octet-stream";
  const form = new URLSearchParams();
  form.set("file_name", file.name);
  form.set("content_type", contentType);

  const { data } = await axios.post(APIS.SCHEMA_UPLOAD_URL, form, {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });

  const put = String(data?.presigned_url ?? "");
  const read = String(data?.download_presigned_url ?? "");
  if (!put || !read) throw new Error("No upload URL was issued");

  const response = await fetch(browserStorageUrl(put), {
    method: "PUT",
    body: file,
    headers: { "Content-Type": contentType },
  });
  if (!response.ok)
    throw new Error(`The sample could not be uploaded (${response.status})`);

  return read;
}

/**
 * Asks the service what fields these documents should be read for.
 *
 * Form-encoded rather than multipart, which is what the route publishes — it
 * takes no file of its own, only the URL of one. This app's axios sends JSON by
 * default, and a JSON body here answers without the fields.
 */
export async function generateSchema(
  input: GenerateInput,
): Promise<GeneratedSchema> {
  const form = new URLSearchParams();
  if (input.collectionName?.trim())
    form.set("collection_name", input.collectionName.trim());
  form.set("service_description", input.serviceDescription);
  if (input.extractionInstructions?.trim())
    form.set("extraction_instructions", input.extractionInstructions.trim());
  if (input.fileUrl) form.set("file_url", input.fileUrl);

  const { data } = await axios.post(APIS.SCHEMA_GENERATE, form, {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });

  return {
    proposed_fields: asFields(data?.proposed_fields),
    rejected_fields: asRejected(data?.rejected_fields),
  };
}

/** One field as the preview call wants to be told about it. */
export interface PreviewField {
  field_name: string;
  description?: string;
  field_type?: string;
  schema?: Record<string, string>;
}

/** How one field fared on the sample document. */
export interface FieldRun {
  field_name: string;
  status: "extracted" | "not_found";
}

/** A field the document carries that nobody asked for. */
export interface SuggestedField {
  field_name: string;
  description?: string | null;
  field_type?: string | null;
  reason?: string | null;
}

/** What `POST /v1/enrichment/preview` answers. */
export interface SamplePreview {
  extracted: Record<string, unknown>;
  fields_run: FieldRun[];
  suggested_fields: SuggestedField[];
  document: { file_name?: string | null; text_truncated?: boolean | null };
}

/**
 * Runs one document against the fields as they currently stand.
 *
 * Multipart, because this one does take the file. Re-send the current fields
 * every time — the whole point of "Run again" is that the list has been edited
 * since, and a preview of the previous list would be a preview of nothing
 * anybody is about to create.
 *
 * Accepting a suggested field just adds it to the list in the browser; nothing
 * here is saved.
 */
export async function previewSample(
  fields: PreviewField[],
  source: { file?: File; fileUrl?: string },
): Promise<SamplePreview> {
  const body = new FormData();
  body.append("fields", JSON.stringify(fields));
  if (source.file) body.append("file", source.file);
  else if (source.fileUrl) body.append("file_url", source.fileUrl);
  else throw new Error("No document to run");

  const { data } = await axios.post(APIS.SCHEMA_PREVIEW, body, {
    // This axios instance sends JSON by default, and a FormData body becomes
    // an empty JSON object without this.
    headers: { "Content-Type": "multipart/form-data" },
  });

  return {
    extracted:
      data?.extracted && typeof data.extracted === "object"
        ? data.extracted
        : {},
    fields_run: Array.isArray(data?.fields_run)
      ? data.fields_run
          .map((raw: Record<string, unknown>) => ({
            field_name: String(raw?.field_name ?? ""),
            status: raw?.status === "extracted" ? "extracted" : "not_found",
          }))
          .filter((entry: FieldRun) => entry.field_name)
      : [],
    suggested_fields: Array.isArray(data?.suggested_fields)
      ? data.suggested_fields
          .map((raw: Record<string, unknown>) => ({
            field_name: String(raw?.field_name ?? ""),
            description: (raw?.description as string) ?? null,
            field_type: (raw?.field_type as string) ?? null,
            reason: (raw?.reason as string) ?? null,
          }))
          .filter((entry: SuggestedField) => entry.field_name)
      : [],
    document: {
      file_name: data?.document?.file_name ?? null,
      text_truncated: data?.document?.text_truncated ?? null,
    },
  };
}
