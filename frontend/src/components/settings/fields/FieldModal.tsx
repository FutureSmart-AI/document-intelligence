"use client";

import { useMemo, useState } from "react";

import {
  Btn,
  Chip,
  FieldHelp,
  FieldLabel,
  Input,
  LinkBtn,
  Modal,
  Select,
  Textarea,
  ToggleRow,
} from "@/components/ui";
import { distinctTypes, useFieldTypes } from "@/data/fieldCatalog";
import { typeInWords } from "@/platform/components/rag/dashboard/knowledge/enrichment-config-modal/utils";
import { cn } from "@/platform/utils";

import {
  isGroupType,
  isListType,
  loreFor,
  suggestWireType,
} from "./fieldTypeLore";

/**
 * One field, as the dialog holds it while it is being written.
 *
 * The same shape the shared enrichment form keeps a row in, so the panel can
 * hand what comes back straight to `saveField` without a second translation
 * layer: `sample_values` is the textarea's newline-separated text, `schema` the
 * JSON string the form validates, `field_type` the wire value and empty when
 * the service is the one to work it out.
 */
export interface FieldDialogValue {
  field_name: string;
  description: string;
  field_type: string;
  field_type_label: string | null;
  sample_values: string;
  restrict_to_choices: boolean;
  max_items: number | string | null;
  is_active: boolean;
  is_required: boolean;
  schema: string | null;
  is_custom: boolean;
}

const columnsOf = (schema: string | null): string[] => {
  if (!schema) return [];
  try {
    const parsed = JSON.parse(schema);
    return parsed && typeof parsed === "object" ? Object.keys(parsed) : [];
  } catch {
    return [];
  }
};

/**
 * The columns box back to a schema, keeping the type of every column that
 * survived the edit.
 *
 * A column's type is not asked for here — the artifact's dialog takes names
 * only — so rebuilding one from scratch would quietly re-type every column as
 * text. New names get `str`, which is what the create screen defaults them to.
 */
const schemaFrom = (
  names: string[],
  previous: string | null,
): string | null => {
  if (!names.length) return null;
  let existing: Record<string, unknown> = {};
  try {
    existing = previous ? (JSON.parse(previous) ?? {}) : {};
  } catch {
    existing = {};
  }
  const next: Record<string, string> = {};
  names.forEach((name) => {
    const carried = (existing as Record<string, string>)[name];
    next[name] = typeof carried === "string" && carried ? carried : "str";
  });
  return JSON.stringify(next, null, 2);
};

const linesOf = (text: string): string[] =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

/**
 * Add or edit one extracted field.
 *
 * Two things this dialog does that the shared page's inline form does not, and
 * both come from the beta design: the copy is written for the person who owns
 * the documents rather than for whoever wrote the API, and the type is
 * explained rather than demanded — what it will look like in the table, and why
 * it was read that way.
 *
 * The type rules are the live service's, checked against it rather than assumed
 * (2026-09-16):
 *
 * - **Creating**, a `field_type` sent by hand is honoured as sent. Sent nothing,
 *   the service resolves the type from the description and answers with its
 *   reasoning, which the save reports. A `field_type_label` on its own is
 *   stored as the label while the type is resolved separately — so a picked
 *   type is sent as a type, never as a label.
 * - **Editing**, the type is fixed: `field_type` answers "Cannot change
 *   field_type" and skips the whole row, and a new `field_type_label` renames
 *   what you see without re-typing anything. So the picker is locked here and
 *   says why, rather than offering a change the service will refuse.
 */
export default function FieldModal({
  open,
  mode,
  value,
  saving,
  onSave,
  onClose,
}: {
  open: boolean;
  mode: "add" | "edit";
  value: FieldDialogValue;
  saving?: boolean;
  onSave: (next: FieldDialogValue) => void;
  onClose: () => void;
}) {
  const editing = mode === "edit";
  const { data: types = [] } = useFieldTypes();

  const [name, setName] = useState(value.field_name);
  const [description, setDescription] = useState(value.description);
  const [samples, setSamples] = useState(value.sample_values);
  const [columns, setColumns] = useState(columnsOf(value.schema).join("\n"));
  const [maxItems, setMaxItems] = useState(
    value.max_items === null || value.max_items === undefined
      ? ""
      : String(value.max_items),
  );
  const [restrict, setRestrict] = useState(value.restrict_to_choices);
  const [required, setRequired] = useState(value.is_required);
  const [active, setActive] = useState(value.is_active);
  // An existing field already has a resolved type; a new one has whatever the
  // description suggests until somebody picks for themselves.
  const [pickedType, setPickedType] = useState<string | null>(
    editing ? value.field_type : null,
  );
  const [problem, setProblem] = useState<string | null>(null);

  const suggested = suggestWireType(description, linesOf(samples));
  const wireType = pickedType ?? suggested;

  /**
   * One row per wire type, with the catalogue's words told apart where two
   * types share them — Number (whole) and Number (decimal) are different
   * choices, and folding them into one silently picked whole numbers.
   */
  const options = useMemo(
    () =>
      distinctTypes(types).map((entry) => ({
        label: entry.field_type_label,
        wire: entry.field_type,
      })),
    [types],
  );

  const lore = loreFor(wireType);
  const described = Boolean(description.trim() || samples.trim());
  const group = isGroupType(types, wireType);
  const list = isListType(wireType);

  const typeName = editing
    ? typeInWords({
        field_type: value.field_type,
        field_type_label: value.field_type_label,
      })
    : (options.find((option) => option.wire === wireType)?.label ??
      typeInWords({ field_type: wireType }));

  const save = () => {
    const trimmedName = name.trim().replace(/\s+/g, "_").toLowerCase();
    if (!trimmedName) {
      setProblem("Give the field a name.");
      return;
    }
    if (!description.trim()) {
      setProblem(
        "Say what this field should capture — it is what we go looking for.",
      );
      return;
    }
    if (restrict && !linesOf(samples).length) {
      setProblem(
        "Turn off “only accept these values”, or list the values to accept.",
      );
      return;
    }
    setProblem(null);
    onSave({
      ...value,
      field_name: editing ? value.field_name : trimmedName,
      description: description.trim(),
      // Editing never sends a type: the service refuses to change one, and a
      // label on its own would only rename the column while it stays what it
      // is. Creating sends the picked type, or nothing at all so the service
      // resolves it from the description and says why.
      field_type: editing ? value.field_type : (pickedType ?? ""),
      field_type_label: editing ? value.field_type_label : null,
      sample_values: samples,
      restrict_to_choices: restrict,
      max_items: list && maxItems.trim() ? Number(maxItems.trim()) : null,
      is_active: active,
      is_required: required,
      schema: group ? schemaFrom(linesOf(columns), value.schema) : null,
    });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? "Edit field" : "Add a field"}
      width="680px"
      footer={
        <>
          <Btn data-demo="field-cancel" onClick={onClose} disabled={saving}>
            Cancel
          </Btn>
          <Btn
            variant="primary"
            data-demo="field-save"
            onClick={save}
            disabled={saving}
          >
            {saving ? "Saving…" : editing ? "Save changes" : "Add field"}
          </Btn>
        </>
      }
    >
      <p className="mt-0 mb-2 text-[12px] text-ink-2">
        A field is one detail pulled out of every document in this collection.
      </p>
      <Chip tone="muted" className="mb-3.5">
        {value.is_custom ? "Added by your team" : "Built-in field"}
      </Chip>

      <div className="mb-3.5">
        <FieldLabel required htmlFor="di-field-name">
          Field name
        </FieldLabel>
        <FieldHelp className="mt-0 mb-1.5">
          Short and lowercase — <span className="font-mono">skills</span>,{" "}
          <span className="font-mono">total_amount</span>.{" "}
          <span className="font-semibold text-ink-3">
            🔒 Can&rsquo;t be changed after you save.
          </span>
        </FieldHelp>
        <Input
          data-demo="field-name-input"
          id="di-field-name"
          value={name}
          disabled={editing}
          placeholder="e.g. skills"
          onChange={(event) => setName(event.target.value)}
        />
      </div>

      <div className="mb-3.5">
        <FieldLabel required htmlFor="di-field-desc">
          What should this field capture?
        </FieldLabel>
        <FieldHelp className="mt-0 mb-1.5">
          Describe it the way you&rsquo;d explain it to a new colleague. This is
          what we go looking for, so be specific.
        </FieldHelp>
        <Textarea
          data-demo="field-desc-input"
          id="di-field-desc"
          value={description}
          placeholder="e.g. Every technical skill listed anywhere in the CV, including tools and programming languages."
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>

      <div
        data-demo="field-type-panel"
        className="mb-3.5 rounded-di-md border border-accent-soft bg-accent-soft/70 p-3.5"
      >
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="text-[11px] font-bold tracking-[0.04em] text-ink-3 uppercase">
            Read this as
          </span>
          {editing ? (
            <span className="rounded-di-md border border-line bg-surface px-2.5 py-1.5 text-[13px] font-semibold text-ink">
              {typeName}
            </span>
          ) : (
            <Select
              fit
              data-demo="field-type-select"
              aria-label="What this field is read as"
              value={wireType}
              className="min-w-[170px]"
              onChange={(event) => setPickedType(event.target.value)}
            >
              {options.length ? (
                options.map((option) => (
                  <option key={option.label} value={option.wire}>
                    {option.label}
                  </option>
                ))
              ) : (
                <option value={wireType}>{typeName}</option>
              )}
            </Select>
          )}
          <span className="flex-1" />
          {!editing && pickedType && (
            <LinkBtn
              data-demo="field-type-auto"
              title="Go back to working the type out from the description"
              onClick={() => setPickedType(null)}
            >
              Work it out for me
            </LinkBtn>
          )}
        </div>

        <FieldHelp className="mt-2">
          {editing
            ? "Fixed once the field exists — the service worked it out from the description when it was created. Add a new field if you need it read another way."
            : "Worked out from what you write above. Change it if we get it wrong."}
        </FieldHelp>
        <p className="mt-1.5 mb-0 text-[12.5px] leading-relaxed text-ink-2">
          {editing
            ? lore.why
            : pickedType
              ? `${lore.why} You picked this yourself, so we'll send it as you chose it.`
              : described
                ? `${lore.why} We'll confirm it against your description when you save.`
                : "Describe the field above and we'll work out what it should be read as, and tell you why."}
        </p>

        <div className="mt-2.5">
          <span className="text-[11px] font-bold tracking-[0.04em] text-ink-3 uppercase">
            In your table it looks like
          </span>
          <div
            data-demo="field-type-sample"
            className="mt-1.5 rounded-di-sm border border-line bg-surface px-2.5 py-2 text-[12.5px]"
          >
            {described || pickedType || editing ? (
              lore.sample
            ) : (
              <span className="text-ink-3">nothing yet</span>
            )}
          </div>
        </div>
      </div>

      {list && (
        <div className="mb-3.5 max-w-[320px]">
          <FieldLabel htmlFor="di-field-max">Most values to keep</FieldLabel>
          <FieldHelp className="mt-0 mb-1.5">
            Leave empty to keep all of them.
          </FieldHelp>
          <Input
            id="di-field-max"
            type="number"
            min={1}
            value={maxItems}
            placeholder="Leave empty to keep all"
            onChange={(event) => setMaxItems(event.target.value)}
          />
        </div>
      )}

      {group && (
        <div className="mb-3.5">
          <FieldLabel htmlFor="di-field-columns">
            What goes inside each group
          </FieldLabel>
          <FieldHelp className="mt-0 mb-1.5">
            One name per line — these become the columns inside this field.
          </FieldHelp>
          <Textarea
            id="di-field-columns"
            value={columns}
            className="min-h-[84px] font-mono text-[12.5px]"
            placeholder={"company\nrole\nstart_date\nend_date"}
            onChange={(event) => setColumns(event.target.value)}
          />
        </div>
      )}

      <div className="grid gap-3.5 md:grid-cols-2">
        <div>
          <FieldLabel htmlFor="di-field-samples">Accepted values</FieldLabel>
          <FieldHelp className="mt-0 mb-1.5">
            One per line. Leave empty to accept anything.
          </FieldHelp>
          <Textarea
            data-demo="field-values-input"
            id="di-field-samples"
            value={samples}
            className="min-h-[90px]"
            placeholder={"B.Tech\nB.E\nM.Tech"}
            onChange={(event) => setSamples(event.target.value)}
          />
        </div>
        <div>
          <div className="mb-1.5 text-[11px] font-bold tracking-[0.04em] text-ink-3 uppercase">
            Rules
          </div>
          <div
            data-demo="field-rules"
            className="rounded-di-md border border-line bg-sunken px-3.5 py-1"
          >
            <ToggleRow
              label="Only accept these values"
              desc="Anything else is left blank. Good for fixed lists like degree types."
              checked={restrict}
              onChange={setRestrict}
            />
            <ToggleRow
              className="border-t border-line-soft"
              label="Every document must have this"
              desc="A document missing it is flagged instead of silently left blank."
              checked={required}
              onChange={setRequired}
            />
            <ToggleRow
              className="border-t border-line-soft"
              label="Use this field"
              desc="Turn off to stop pulling this out of new documents. Values already pulled out are kept."
              checked={active}
              onChange={setActive}
            />
          </div>
        </div>
      </div>

      {problem && (
        <p
          role="alert"
          className={cn(
            "mt-3.5 mb-0 rounded-di-md border border-danger-text/30 bg-danger-bg",
            "px-3 py-2 text-[12.5px] font-semibold text-danger-text",
          )}
        >
          {problem}
        </p>
      )}
    </Modal>
  );
}
