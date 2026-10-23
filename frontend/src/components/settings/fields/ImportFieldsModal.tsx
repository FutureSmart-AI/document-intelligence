"use client";

import { useState } from "react";

import { Btn, FieldHelp, Modal, Textarea } from "@/components/ui";

/** One field as an import file describes it, before the service sees it. */
export interface ImportedField {
  field_name: string;
  description: string;
  field_type?: string;
  level: "document" | "chunk";
  is_custom: boolean;
  sample_values: string[];
  restrict_to_choices: boolean;
  max_items: number | null;
  is_active: boolean;
  is_required: boolean;
  schema: unknown;
}

const asStringList = (raw: unknown): string[] => {
  if (Array.isArray(raw))
    return raw.map((entry) => String(entry)).filter(Boolean);
  if (typeof raw === "string")
    return raw
      .split(/[\n,]/)
      .map((entry) => entry.trim())
      .filter(Boolean);
  return [];
};

const asField = (raw: unknown, level: "document" | "chunk"): ImportedField => {
  const entry = (raw ?? {}) as Record<string, unknown>;
  const fieldType = entry.field_type ? String(entry.field_type) : undefined;
  return {
    field_name: String(entry.field_name ?? "")
      .trim()
      .replace(/\s+/g, "_")
      .toLowerCase(),
    description: String(entry.description ?? "").trim(),
    // Left off when the file does not say: the service then works the type out
    // from the description, the same as a field added by hand, rather than
    // every imported field landing as text.
    ...(fieldType ? { field_type: fieldType } : {}),
    level: (entry.level === "chunk" ? "chunk" : level) as "document" | "chunk",
    is_custom: true,
    sample_values: asStringList(entry.sample_values),
    restrict_to_choices: Boolean(entry.restrict_to_choices),
    max_items:
      typeof entry.max_items === "number" ? (entry.max_items as number) : null,
    is_active: entry.is_active === undefined ? true : Boolean(entry.is_active),
    is_required: Boolean(entry.is_required),
    schema: entry.schema ?? null,
  };
};

/**
 * Reads the two shapes a field file comes in.
 *
 * `document_fields` / `section_fields` is what this product's own export writes;
 * a bare array (or `fields`) is what people paste out of a spec or an older
 * export. Anything without a name or a description is dropped here rather than
 * sent — the service rejects the whole call over one blank description, which
 * would read as "the import is broken" instead of "row 7 has no description".
 */
export const parseImport = (
  text: string,
): { fields: ImportedField[]; error: string | null } => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { fields: [], error: "That is not valid JSON." };
  }

  const source = (parsed ?? {}) as Record<string, unknown>;
  const collected: ImportedField[] = [];
  if (Array.isArray(parsed)) {
    parsed.forEach((raw) => collected.push(asField(raw, "document")));
  } else {
    (Array.isArray(source.document_fields)
      ? source.document_fields
      : []
    ).forEach((raw) => collected.push(asField(raw, "document")));
    (Array.isArray(source.section_fields) ? source.section_fields : []).forEach(
      (raw) => collected.push(asField(raw, "chunk")),
    );
    (Array.isArray(source.fields) ? source.fields : []).forEach((raw) =>
      collected.push(asField(raw, "document")),
    );
  }

  const usable = collected.filter(
    (field) => field.field_name && field.description,
  );
  if (!usable.length) {
    return {
      fields: [],
      error:
        "No fields to import — every field needs a name and a description of what it captures.",
    };
  }
  return { fields: usable, error: null };
};

/** Paste a field file in, and add everything it describes in one go. */
export default function ImportFieldsModal({
  open,
  importing,
  onImport,
  onClose,
}: {
  open: boolean;
  importing?: boolean;
  onImport: (fields: ImportedField[]) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const submit = () => {
    const { fields, error } = parseImport(text);
    if (error) {
      setProblem(error);
      return;
    }
    setProblem(null);
    onImport(fields);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Import fields from JSON"
      width="620px"
      footer={
        <>
          <Btn data-demo="import-cancel" onClick={onClose} disabled={importing}>
            Cancel
          </Btn>
          <Btn variant="primary" onClick={submit} disabled={importing}>
            {importing ? "Importing…" : "Import fields"}
          </Btn>
        </>
      }
    >
      <FieldHelp className="mt-0 mb-2">
        Paste JSON to add several fields at once. Uses{" "}
        <span className="font-mono">document_fields</span> /{" "}
        <span className="font-mono">section_fields</span> keys, or a plain list
        of fields. A field with no type is read the same way a field you add by
        hand is — worked out from its description.
      </FieldHelp>
      <Textarea
        data-demo="import-json"
        value={text}
        onChange={(event) => setText(event.target.value)}
        className="min-h-[180px] font-mono text-[12px]"
        placeholder={
          '{"document_fields": [{"field_name": "vendor", "description": "Who issued the invoice."}]}'
        }
      />
      {problem && (
        <p
          role="alert"
          className="mt-3 mb-0 rounded-di-md border border-line bg-danger-bg px-3 py-2 text-[12.5px] font-semibold text-danger-text"
        >
          {problem}
        </p>
      )}
    </Modal>
  );
}
