"use client";

import { useState } from "react";

import { Btn, CheckRow, Modal } from "@/components/ui";

/**
 * Removing a field, and the one decision that comes with it.
 *
 * Dropping the field stops it being pulled out of new documents; the values
 * already pulled out of old ones are a separate thing, and the service keeps
 * them unless it is told not to. Asked here rather than assumed, because
 * neither answer is recoverable.
 */
export default function DeleteFieldModal({
  fieldName,
  deleting,
  onConfirm,
  onClose,
}: {
  fieldName: string | null;
  deleting?: boolean;
  onConfirm: (deleteValues: boolean) => void;
  onClose: () => void;
}) {
  const [deleteValues, setDeleteValues] = useState(false);

  return (
    <Modal
      open={Boolean(fieldName)}
      onClose={onClose}
      title="Remove this field?"
      width="520px"
      footer={
        <>
          <Btn data-demo="delete-cancel" onClick={onClose} disabled={deleting}>
            Cancel
          </Btn>
          <Btn
            variant="primary"
            data-demo="field-delete-confirm"
            disabled={deleting}
            onClick={() => onConfirm(deleteValues)}
            className="border-danger-text bg-danger-text hover:border-danger-text hover:bg-danger-text"
          >
            {deleting ? "Removing…" : "Remove field"}
          </Btn>
        </>
      }
    >
      <p className="mt-0 mb-3.5 text-[13px] leading-relaxed text-ink">
        <span className="font-mono font-bold">{fieldName}</span> will no longer
        be pulled out of documents you add from now on. This cannot be undone.
      </p>
      <CheckRow
        checked={deleteValues}
        onChange={setDeleteValues}
        label="Also delete the values already pulled out"
        desc="Leave this off to keep what has already been extracted from documents in this collection."
      />
    </Modal>
  );
}
