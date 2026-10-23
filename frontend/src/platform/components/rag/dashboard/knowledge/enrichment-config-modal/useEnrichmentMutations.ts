"use client";

import { useMutation } from "@tanstack/react-query";
import { get, map } from "lodash-es";
import type { UseFormReturn } from "react-hook-form";

import { useToast } from "@/platform/components/ToastContext";
import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS, FIELD_META } from "./constants";
import type { EnrichmentFormData } from "./schemas";
import type {
  EnrichmentField,
  EnrichmentSaveResponse,
  FieldKey,
  TypeMode,
} from "./types";
import { toPayload } from "./utils";

interface UseEnrichmentMutationsArgs {
  knowledgeId: string | null;
  documentMetadataEnabled: boolean;
  chunkMetadataEnabled: boolean;
  form: UseFormReturn<EnrichmentFormData>;
  setEditingIndex: (value: number | null) => void;
  refetch: () => void;
  /** See `TypeMode`. Defaults to the explicit picker this screen has always had. */
  typeMode?: TypeMode;
}

export const useEnrichmentMutations = ({
  knowledgeId,
  documentMetadataEnabled,
  chunkMetadataEnabled,
  form,
  setEditingIndex,
  refetch,
  typeMode = "explicit",
}: UseEnrichmentMutationsArgs) => {
  const { toast } = useToast();
  const resolved = typeMode === "resolved";

  /**
   * What the service decided, said out loud.
   *
   * `type_resolutions` is how it read a description — the type is never taken
   * from a label — and `summary.warnings` is anything it declined to do, most
   * often a re-type refused because a document already holds a value for that
   * field. The warning is shown **verbatim**: it is the only account of why the
   * type stayed as it was, and rewording it would be inventing a reason.
   */
  const reportServerDecisions = (data: EnrichmentSaveResponse | undefined) => {
    if (!data) return;
    (data.type_resolutions ?? []).forEach((resolution) => {
      const label = resolution.field_type_label || resolution.field_type;
      toast.info(
        resolution.reason
          ? `"${resolution.field_name}" saved as ${label} — ${resolution.reason}`
          : `"${resolution.field_name}" saved as ${label}.`,
      );
    });
    (data.summary?.warnings ?? []).forEach((warning) => {
      toast.warning(warning);
    });
  };

  const { mutateAsync: saveFields, isPending: isSaving } = useMutation({
    mutationFn: async (payload: {
      fields: Array<
        Omit<
          EnrichmentField,
          "id" | "knowledge_id" | "created_at" | "updated_at" | "metadata"
        > & { id?: number }
      >;
    }) => {
      if (!knowledgeId) {
        throw new Error("Knowledge ID missing");
      }
      // A field with no id is a new one. PATCH would create it too, but only
      // POST answers with the reasoning behind the type it resolved from the
      // description — which is the whole point of a screen that never asks for
      // a type. Existing fields always go by PATCH.
      const creating =
        resolved && payload.fields.every((field) => field.id === undefined);
      const url = buildApiUrl(
        creating ? APIS.POST_ENRICHMENT_CONFIG : APIS.PATCH_ENRICHMENT_CONFIG,
        { knowledge_id: knowledgeId },
      );
      const response = creating
        ? await axios.post(url, { fields: payload.fields })
        : await axios.patch(url, { fields: payload.fields });
      return response.data as EnrichmentSaveResponse;
    },
    onSuccess: (responseData) => {
      if (responseData?.message) toast.success(responseData.message);
      reportServerDecisions(responseData);
      setEditingIndex(null);
      refetch();
    },
    onError: (error: any) => {
      const errorDetail = error?.response?.data?.detail;
      if (typeof errorDetail === "object" && errorDetail?.details) {
        const fieldErrors = map(
          get(errorDetail, "details"),
          (d: any) => `${d.field_name}: ${d.error}`,
        ).join(", ");
        toast.error(fieldErrors || "Failed to save enrichment configuration");
      } else {
        toast.error(
          errorDetail ||
            error?.message ||
            "Failed to save enrichment configuration",
        );
      }
    },
  });

  const { mutateAsync: deleteFields, isPending: isDeleting } = useMutation({
    mutationFn: async (payload: {
      fieldIds: number[];
      deleteMetadata: boolean;
    }) => {
      if (!knowledgeId) {
        throw new Error("Knowledge ID missing");
      }
      const url = buildApiUrl(APIS.DELETE_ENRICHMENT_FIELDS, {
        knowledge_id: knowledgeId,
      });
      const response = await axios.delete(url, {
        data: { field_ids: payload.fieldIds },
        params: {
          delete_metadata: payload.deleteMetadata,
        },
      });
      return response.data;
    },
    onSuccess: (responseData) => {
      if (responseData?.message) toast.success(responseData.message);
      refetch();
    },
    onError: (error: any) => {
      toast.error(
        error?.response?.data?.detail ||
          error?.message ||
          "Failed to delete fields",
      );
    },
  });

  const validateField = async (index: number) => {
    const fieldValue = form.watch(`fields.${index}`);
    if (!fieldValue) {
      toast.error("Unable to find field data.");
      return null;
    }

    const isValid = await form.trigger(`fields.${index}`);
    const allFieldsValid = await form.trigger("fields");

    const fieldErrors = form.formState.errors.fields?.[index];
    const formErrors = form.formState.errors.fields;

    if (!isValid || !allFieldsValid || fieldErrors || formErrors) {
      const errorMessages: string[] = [];

      if (fieldErrors) {
        Object.entries(fieldErrors).forEach(([key, error]) => {
          if (error) {
            const message =
              typeof error === "string"
                ? error
                : error && typeof error === "object" && "message" in error
                  ? (error.message as string)
                  : null;
            if (message) {
              const fieldLabel = get(FIELD_META, key as FieldKey)?.label || key;
              errorMessages.push(`${fieldLabel}: ${message}`);
            }
          }
        });
      }

      if (formErrors) {
        formErrors.forEach((fieldError, idx) => {
          if (idx === index && fieldError?.field_name) {
            const nameError =
              typeof fieldError.field_name === "string"
                ? fieldError.field_name
                : fieldError.field_name?.message;
            if (nameError) {
              const errorText = `Field name: ${nameError}`;
              if (!errorMessages.some((msg) => msg.includes(errorText))) {
                errorMessages.push(errorText);
              }
            }
          }
        });
      }

      if (errorMessages.length > 0) {
        const firstErrorKey = Object.keys(fieldErrors || {})[0];
        if (firstErrorKey) {
          const errorField = document.querySelector(
            `[name="fields.${index}.${firstErrorKey}"]`,
          );
          errorField?.scrollIntoView({
            behavior: "smooth",
            block: "center",
          });
          setTimeout(() => {
            (errorField as HTMLElement | null)?.focus();
          }, 100);
        }

        toast.error(
          errorMessages.length === 1
            ? errorMessages[0]
            : `Please fix ${errorMessages.length} validation error${
                errorMessages.length > 1 ? "s" : ""
              }. See details below.`,
        );
        return null;
      }

      toast.error("Please fix the validation errors before saving.");
      return null;
    }

    if (fieldValue.level === "document" && !documentMetadataEnabled) {
      toast.error("Document-level metadata is disabled in Knowledge Config.");
      return null;
    }
    if (fieldValue.level === "chunk" && !chunkMetadataEnabled) {
      toast.error("Chunk-level metadata is disabled in Knowledge Config.");
      return null;
    }

    return fieldValue;
  };

  const saveField = async (index: number) => {
    const fieldValue = await validateField(index);
    if (!fieldValue) return;

    /**
     * In `resolved` mode the type is the service's to work out — but only until
     * the field exists.
     *
     * Creating, a `field_type` sent by hand is honoured as sent, so a screen
     * that offers the choice has to be able to send it; left empty, `toPayload`
     * drops it anyway and the service resolves it from the description and says
     * why. Editing, the service answers "Cannot change field_type" and **skips
     * the whole row**, taking every other edit in the call with it, so the type
     * is never sent again. (Live-checked 2026-09-16.)
     */
    const isNew = !fieldValue.id;
    const payload = toPayload(
      { fields: [fieldValue] },
      { omitFieldType: resolved && !isNew },
    );
    await saveFields({ fields: payload });
  };

  const deleteField = async (fieldId: number, deleteMetadata: boolean) => {
    await deleteFields({ fieldIds: [fieldId], deleteMetadata });
  };

  // Import multiple fields from JSON
  const { mutateAsync: importFieldsMutation, isPending: isImporting } =
    useMutation({
      mutationFn: async (
        fieldsToImport: Array<{
          field_name: string;
          description: string;
          field_type: string;
          level: "document" | "chunk";
          is_custom: boolean;
          sample_values: string[];
          restrict_to_choices: boolean;
          max_items: number | null;
          is_active: boolean;
          is_required: boolean;
          schema: any;
        }>,
      ) => {
        if (!knowledgeId) {
          throw new Error("Knowledge ID missing");
        }
        const url = buildApiUrl(APIS.PATCH_ENRICHMENT_CONFIG, {
          knowledge_id: knowledgeId,
        });
        const response = await axios.patch(url, { fields: fieldsToImport });
        return response.data;
      },
      onSuccess: (responseData) => {
        if (responseData?.message) toast.success(responseData.message);
        refetch();
      },
      onError: (error: any) => {
        const errorDetail = error?.response?.data?.detail;
        if (typeof errorDetail === "object" && errorDetail?.details) {
          const fieldErrors = map(
            get(errorDetail, "details"),
            (d: any) => `${d.field_name}: ${d.error}`,
          ).join(", ");
          toast.error(fieldErrors || "Failed to import fields");
        } else {
          toast.error(
            errorDetail || error?.message || "Failed to import fields",
          );
        }
      },
    });

  const importFields = async (
    fieldsToImport: Array<{
      field_name: string;
      description: string;
      field_type: string;
      level: "document" | "chunk";
      is_custom: boolean;
      sample_values: string[];
      restrict_to_choices: boolean;
      max_items: number | null;
      is_active: boolean;
      is_required: boolean;
      schema: any;
    }>,
  ) => {
    await importFieldsMutation(fieldsToImport);
  };

  return {
    saveField,
    deleteField,
    importFields,
    isSaving,
    isDeleting,
    isImporting,
  };
};
