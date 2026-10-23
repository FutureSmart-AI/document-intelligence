"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { zodResolver } from "@hookform/resolvers/zod";
import { some } from "lodash-es";
import {
  useFieldArray,
  useForm,
  type UseFieldArrayReturn,
} from "react-hook-form";

import { enrichmentFormSchema, type EnrichmentFormData } from "./schemas";
import type { EnrichmentConfigResponse, FieldToDelete } from "./types";
import { toFormData } from "./utils";

interface UseEnrichmentFormStateArgs {
  data?: EnrichmentConfigResponse;
  open: boolean;
  isFetching: boolean;
  documentMetadataEnabled: boolean;
  chunkMetadataEnabled: boolean;
}

interface UseEnrichmentFormStateReturn {
  form: ReturnType<typeof useForm<EnrichmentFormData>>;
  fields: UseFieldArrayReturn<EnrichmentFormData, "fields", "id">["fields"];
  append: UseFieldArrayReturn<EnrichmentFormData, "fields", "id">["append"];
  remove: UseFieldArrayReturn<EnrichmentFormData, "fields", "id">["remove"];
  replace: UseFieldArrayReturn<EnrichmentFormData, "fields", "id">["replace"];
  metadataReady: boolean;
  editingIndex: number | null;
  setEditingIndex: (value: number | null) => void;
  fieldToDelete: FieldToDelete | null;
  setFieldToDelete: (value: FieldToDelete | null) => void;
  pendingLevelSelection: boolean;
  setPendingLevelSelection: (value: boolean) => void;
  watchedFields: EnrichmentFormData["fields"];
  disableAdd: boolean;
  showDisabledLevelWarning: boolean;
  handleReset: () => void;
}

export const useEnrichmentFormState = ({
  data,
  open,
  isFetching,
  documentMetadataEnabled,
  chunkMetadataEnabled,
}: UseEnrichmentFormStateArgs): UseEnrichmentFormStateReturn => {
  const metadataReady = documentMetadataEnabled || chunkMetadataEnabled;
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [fieldToDelete, setFieldToDelete] = useState<FieldToDelete | null>(
    null,
  );
  const [pendingLevelSelection, setPendingLevelSelection] = useState(false);

  const form = useForm<EnrichmentFormData>({
    resolver: zodResolver(enrichmentFormSchema),
    defaultValues: { fields: [] },
    criteriaMode: "all",
    mode: "onChange",
  });

  const { reset, watch, control } = form;

  const { fields, append, remove, replace } = useFieldArray({
    control,
    name: "fields",
  });

  useEffect(() => {
    if (!open) {
      setEditingIndex(null);
      setPendingLevelSelection(false);
      return;
    }

    if (data?.fields) {
      const formData = toFormData(data.fields);
      replace(formData.fields);
      reset(formData as EnrichmentFormData);
      setEditingIndex(null);
      setPendingLevelSelection(false);
    } else if (!data && !isFetching) {
      replace([]);
      reset({ fields: [] } as EnrichmentFormData);
      setEditingIndex(null);
      setPendingLevelSelection(false);
    }
  }, [data, open, isFetching, replace, reset]);

  const handleReset = useCallback(() => {
    if (!data?.fields) {
      reset({ fields: [] } as EnrichmentFormData);
      replace([]);
      return;
    }
    const formData = toFormData(data.fields);
    reset(formData as EnrichmentFormData);
    replace(formData.fields);
  }, [data, reset, replace]);

  const watchedFields = watch("fields");

  const showDisabledLevelWarning = useMemo(() => {
    if (!metadataReady) return false;
    const hasDocumentFields = some(
      watchedFields,
      (field) => field.level === "document",
    );
    const hasChunkFields = some(
      watchedFields,
      (field) => field.level === "chunk",
    );

    return (
      (hasDocumentFields && !documentMetadataEnabled) ||
      (hasChunkFields && !chunkMetadataEnabled)
    );
  }, [
    watchedFields,
    metadataReady,
    documentMetadataEnabled,
    chunkMetadataEnabled,
  ]);

  const disableAdd =
    !metadataReady ||
    isFetching ||
    editingIndex !== null ||
    pendingLevelSelection;

  return {
    form,
    fields,
    append,
    remove,
    replace,
    metadataReady,
    editingIndex,
    setEditingIndex,
    fieldToDelete,
    setFieldToDelete,
    pendingLevelSelection,
    setPendingLevelSelection,
    watchedFields,
    disableAdd,
    showDisabledLevelWarning,
    handleReset,
  };
};
