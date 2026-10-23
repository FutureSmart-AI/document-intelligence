"use client";

import { useState } from "react";

import Icon from "@/components/Icon";
import { Btn, EmptyNote, Pill } from "@/components/ui";
import { useEnrichmentData } from "@/platform/components/rag/dashboard/knowledge/enrichment-config-modal/useEnrichmentData";
import { useEnrichmentFormState } from "@/platform/components/rag/dashboard/knowledge/enrichment-config-modal/useEnrichmentFormState";
import { useEnrichmentMutations } from "@/platform/components/rag/dashboard/knowledge/enrichment-config-modal/useEnrichmentMutations";
import { toFormData } from "@/platform/components/rag/dashboard/knowledge/enrichment-config-modal/utils";

import DeleteFieldModal from "./DeleteFieldModal";
import DocumentFieldsTable, { type FieldRow } from "./DocumentFieldsTable";
import FieldModal, { type FieldDialogValue } from "./FieldModal";
import ImportFieldsModal, { type ImportedField } from "./ImportFieldsModal";

/**
 * The fields this collection pulls out of its documents.
 *
 * This product's own screen rather than the shared `EnrichmentConfigPage`: the
 * two show the same rows through the same hooks — one fetch, one form array,
 * one save — but they are written for different readers. The shared page is the
 * platform's, worded in the API's terms (levels, immutability, properties) for
 * whoever is wiring a knowledge base up; this one is the beta design's, worded
 * for the person who owns the documents, and it edits a field in a dialog
 * instead of unfolding the row into a form the length of the screen.
 *
 * Everything below document level stays out of the way. Every field added here
 * is a document field, because in this product a field is something each
 * document has; section fields still exist in the service and any that a
 * collection already carries are shown, read-only in all but name, under a
 * disclosure rather than as a second half of the screen.
 */
export default function DocumentFieldsPanel({
  knowledgeId,
  documentMetadataEnabled,
  chunkMetadataEnabled,
}: {
  knowledgeId: string;
  documentMetadataEnabled: boolean;
  chunkMetadataEnabled: boolean;
}) {
  const { data, isFetching, isError, refetch } = useEnrichmentData(
    knowledgeId,
    true,
  );
  const { form, fields, append, remove, setEditingIndex } =
    useEnrichmentFormState({
      data,
      open: true,
      isFetching,
      documentMetadataEnabled,
      chunkMetadataEnabled,
    });
  const { saveField, deleteField, importFields, isSaving, isDeleting } =
    useEnrichmentMutations({
      knowledgeId,
      documentMetadataEnabled,
      chunkMetadataEnabled,
      form,
      setEditingIndex,
      refetch,
      // The type is the service's to work out — from the description when a
      // field is created, and never again afterwards. See `FieldModal` for what
      // the live service does with a type or a label sent on either call.
      typeMode: "resolved",
    });

  const [dialog, setDialog] = useState<{
    mode: "add" | "edit";
    index: number;
  } | null>(null);
  const [toDelete, setToDelete] = useState<FieldRow | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [showSectionFields, setShowSectionFields] = useState(false);

  const watched = form.watch("fields") ?? [];
  const rowsAt = (level: "document" | "chunk"): FieldRow[] =>
    watched
      .map((field, index) => ({ field, index }))
      // A field being added lives in the same array as the saved ones — it has
      // to, because that array is what `saveField` sends — but it has no id
      // until the service answers, and a half-typed row behind the dialog is
      // not something the collection has. It joins the table when it exists.
      .filter(({ field }) => field?.level === level && Boolean(field?.id))
      .map(({ field, index }) => ({
        index,
        id: Number(field.id ?? 0),
        field_name: field.field_name,
        description: field.description,
        field_type: field.field_type,
        field_type_label: field.field_type_label ?? null,
        is_custom: Boolean(field.is_custom),
        is_required: Boolean(field.is_required),
        is_active: Boolean(field.is_active),
      }));

  const documentRows = rowsAt("document");
  const sectionRows = rowsAt("chunk");

  const startAdd = () => {
    const index = fields.length;
    append({
      id: 0,
      field_name: "",
      description: "",
      // Empty on purpose: the payload then leaves `field_type` off altogether
      // and the service resolves it from the description, answering with the
      // reasoning the save reports back.
      field_type: "",
      field_type_label: null,
      level: "document",
      is_custom: true,
      sample_values: "",
      restrict_to_choices: false,
      max_items: null,
      is_active: true,
      is_required: false,
      schema: null,
    });
    setEditingIndex(index);
    setDialog({ mode: "add", index });
  };

  const closeDialog = () => {
    if (dialog?.mode === "add") remove(dialog.index);
    else if (dialog && data?.fields) {
      // Put the row back as the service last answered with it, so a cancelled
      // edit does not leave the table showing changes nothing saved.
      const current = form.getValues(`fields.${dialog.index}`);
      const original = data.fields.find((field) => field.id === current?.id);
      if (original)
        form.setValue(
          `fields.${dialog.index}`,
          toFormData([original]).fields[0],
        );
    }
    setEditingIndex(null);
    setDialog(null);
  };

  const handleSave = async (next: FieldDialogValue) => {
    if (!dialog) return;
    const current = form.getValues(`fields.${dialog.index}`);
    form.setValue(
      `fields.${dialog.index}`,
      { ...current, ...next },
      { shouldValidate: true, shouldDirty: true },
    );
    try {
      const valid = await form.trigger("fields");
      await saveField(dialog.index);
      // `saveField` reports its own refusals and returns without sending, so a
      // dialog is only finished with when the form was good enough to send.
      if (valid) setDialog(null);
    } catch {
      // The request failed and the shared mutation has already said so; the
      // dialog stays open with what was typed still in it.
    }
  };

  const handleDelete = async (deleteValues: boolean) => {
    if (!toDelete) return;
    try {
      await deleteField(toDelete.id, deleteValues);
    } catch {
      // Reported by the shared mutation.
    }
    setToDelete(null);
  };

  const handleImport = async (imported: ImportedField[]) => {
    try {
      // The wire takes a field with no `field_type` — the service resolves it,
      // the same as a field added by hand — which the shared helper's argument
      // type predates.
      await importFields(imported as Parameters<typeof importFields>[0]);
      setImportOpen(false);
    } catch {
      // Reported by the shared mutation.
    }
  };

  if (isFetching && !data) {
    return <p className="text-[13px] text-ink-2">Reading this collection…</p>;
  }

  if (isError) {
    return (
      <EmptyNote>
        We could not read this collection&rsquo;s fields.{" "}
        <button
          type="button"
          className="cursor-pointer font-bold text-accent hover:underline"
          onClick={() => refetch()}
        >
          Try again
        </button>
      </EmptyNote>
    );
  }

  const busy = isSaving || isDeleting || Boolean(dialog);

  return (
    <div>
      <div
        data-demo="fields-toolbar"
        className="mb-3 flex flex-wrap items-center gap-2"
      >
        <b className="text-[13.5px] text-ink">Document fields</b>
        <Pill tone="count">{documentRows.length}</Pill>
        <span className="text-[12.5px] text-ink-2">
          Extracted once from every document.
        </span>
        <span className="flex-1" />
        <Btn
          size="sm"
          data-demo="import-fields"
          onClick={() => setImportOpen(true)}
          startIcon={<Icon name="dn" className="size-3.5" />}
        >
          Import
        </Btn>
        <Btn
          size="sm"
          variant="primary"
          data-demo="add-field"
          onClick={startAdd}
          disabled={busy}
          startIcon={<Icon name="plus" className="size-3.5" />}
        >
          Add field
        </Btn>
      </div>

      <DocumentFieldsTable
        rows={documentRows}
        busy={busy}
        onEdit={(row) => setDialog({ mode: "edit", index: row.index })}
        onDelete={setToDelete}
        emptyNote="No fields yet. Add one for each detail you want pulled out of every document."
      />

      {sectionRows.length > 0 && (
        <div className="mt-5">
          <button
            type="button"
            onClick={() => setShowSectionFields((open) => !open)}
            className="inline-flex cursor-pointer items-center gap-1.5 text-[12.5px] font-bold text-ink-2 hover:text-ink"
          >
            Section fields — Advanced
            <Icon
              name="caret"
              className={showSectionFields ? "size-3 rotate-180" : "size-3"}
            />
          </button>
          {showSectionFields && (
            <div className="mt-2.5">
              <p className="mt-0 mb-2.5 text-[12.5px] text-ink-2">
                Extracted separately from individual sections, so you can filter
                and search at a more precise level than the whole document. New
                fields are added at document level.
              </p>
              <DocumentFieldsTable
                rows={sectionRows}
                busy={busy}
                onEdit={(row) => setDialog({ mode: "edit", index: row.index })}
                onDelete={setToDelete}
                emptyNote="No section fields."
              />
            </div>
          )}
        </div>
      )}

      {dialog && (
        <FieldModal
          // Remounted per row so the dialog opens on what that field actually
          // holds rather than on whatever the last one did.
          key={`${dialog.mode}-${dialog.index}`}
          open
          mode={dialog.mode}
          saving={isSaving}
          value={
            {
              ...form.getValues(`fields.${dialog.index}`),
            } as unknown as FieldDialogValue
          }
          onSave={handleSave}
          onClose={closeDialog}
        />
      )}

      <DeleteFieldModal
        fieldName={toDelete?.field_name ?? null}
        deleting={isDeleting}
        onConfirm={handleDelete}
        onClose={() => setToDelete(null)}
      />

      <ImportFieldsModal
        open={importOpen}
        onImport={handleImport}
        onClose={() => setImportOpen(false)}
      />
    </div>
  );
}
