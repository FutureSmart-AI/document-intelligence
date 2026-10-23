import { SaveIcon, X } from "lucide-react";

import Button from "@/platform/components/ui/Button";

interface FormActionButtonsProps {
  onSave: () => void;
  onCancel: () => void;
  saveLabel?: string;
  cancelLabel?: string;
  isSaving?: boolean;
  isDisabled?: boolean;
  className?: string;
}

export const FormActionButtons = ({
  onSave,
  onCancel,
  saveLabel = "Save Changes",
  cancelLabel = "Cancel",
  isSaving = false,
  isDisabled = false,
  className = "",
}: FormActionButtonsProps) => {
  return (
    <div className={`flex items-center justify-end gap-3 ${className}`}>
      <Button
        variant="outline"
        size="sm"
        startIcon={<X className="size-4" />}
        onClick={onCancel}
        disabled={isSaving}
      >
        {cancelLabel}
      </Button>
      <Button
        variant="primary"
        size="sm"
        startIcon={<SaveIcon className="size-4" />}
        onClick={onSave}
        disabled={isSaving || isDisabled}
        loading={isSaving}
      >
        {saveLabel}
      </Button>
    </div>
  );
};
