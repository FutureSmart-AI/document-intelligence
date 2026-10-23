import { AlertCircle } from "lucide-react";

import { Modal } from "@/platform/components/ui/Modal";
import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";

import { TYPE_LABELS } from "./constants";

interface DeleteKnowledgeDialogProps {
  open: boolean;
  record: KnowledgeItem | null;
  deleting: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export const DeleteKnowledgeDialog = ({
  open,
  record,
  deleting,
  onClose,
  onConfirm,
}: DeleteKnowledgeDialogProps) => {
  const typeLabel = record
    ? TYPE_LABELS[record.content_type] || record.content_type
    : "";
  const displayName =
    record?.content_type === "url"
      ? record.url || record.file_name
      : record?.file_name;

  return (
    <Modal
      open={open}
      onCancel={onClose}
      onConfirm={onConfirm}
      confirmLoading={deleting}
      confirmText="Yes, Delete"
      cancelText="Cancel"
      variant="danger"
      title={
        <span className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4 text-rose-500" />
          Delete {typeLabel} Item
        </span>
      }
    >
      <div className="text-sm text-slate-700">
        <p>Are you sure you want to delete this item?</p>
        {displayName && (
          <div className="mt-2 rounded-md bg-slate-50 px-3 py-2 text-sm font-medium text-slate-900 break-all">
            {displayName}
          </div>
        )}
        <p className="mt-3 text-slate-500">This action cannot be undone.</p>
      </div>
    </Modal>
  );
};
