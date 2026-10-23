import { useCallback, useState } from "react";

import { useMutation } from "@tanstack/react-query";
import { defaultTo, get, lowerCase } from "lodash-es";

import { useToast } from "@/platform/components/ToastContext";
import { useAuth } from "@/platform/hooks/useAuth";
import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";
import axios from "@/platform/utils/axios";

import { isFileBeingProcessed } from "../utils/fileUtils";

interface UseKnowledgeDeleteOptions {
  onSuccess?: () => void;
}

export const useKnowledgeDelete = ({
  onSuccess,
}: UseKnowledgeDeleteOptions = {}) => {
  const { toast } = useToast();
  const { knowledgeId } = useAuth();
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteRecord, setDeleteRecord] = useState<KnowledgeItem | null>(null);

  const deleteMutation = useMutation({
    mutationFn: async (objectId: string) => {
      const response = await axios.delete(
        `/api/knowledge/v1/${knowledgeId}/documents/${objectId}`,
        { timeout: 120_000 },
      );
      return get(response, "data");
    },
    onSuccess: (responseData) => {
      toast.success(
        defaultTo(get(responseData, "message"), "Deleted successfully"),
      );
      onSuccess?.();
    },
    onError: (error: any) => {
      const errorCode = get(error, "code");
      const errorMessage = get(error, "message");
      if (
        errorCode === "ECONNABORTED" ||
        (errorMessage && lowerCase(errorMessage).includes("timeout"))
      ) {
        toast.error(
          "Delete request timed out. The server may be busy — please try again.",
        );
      } else {
        toast.error(
          defaultTo(
            get(error, "response.data.detail"),
            "Failed to delete item",
          ),
        );
      }
    },
  });

  const handleDelete = useCallback(
    (record: KnowledgeItem) => {
      if (isFileBeingProcessed(record.status)) {
        toast.warning(
          `Cannot delete while item is ${record.status}. Please wait for it to complete.`,
        );
        return;
      }
      setDeleteRecord(record);
      setDeleteModalOpen(true);
    },
    [toast],
  );

  const confirmDelete = useCallback(async () => {
    if (!deleteRecord) return;

    if (isFileBeingProcessed(deleteRecord.status)) {
      toast.warning(
        `Cannot delete while item is ${deleteRecord.status}. Please wait for it to complete.`,
      );
      setDeleteModalOpen(false);
      setDeleteRecord(null);
      return;
    }

    setDeleting(true);
    try {
      await deleteMutation.mutateAsync(deleteRecord.object_id);
    } finally {
      setDeleting(false);
      setDeleteModalOpen(false);
      setDeleteRecord(null);
    }
  }, [deleteRecord, deleteMutation, toast]);

  const closeModal = useCallback(() => {
    setDeleteModalOpen(false);
    setDeleteRecord(null);
  }, []);

  return {
    handleDelete,
    confirmDelete,
    closeModal,
    deleteModalOpen,
    deleting,
    deleteRecord,
  };
};
