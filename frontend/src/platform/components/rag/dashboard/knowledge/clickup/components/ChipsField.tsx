import { useState } from "react";

import { X } from "lucide-react";

import { Button, Input } from "@/platform/components/ui";
import { cn } from "@/platform/utils";

interface ChipsFieldProps {
  chips: string[];
  /** Add a chip from the raw input value. Return false if it was rejected. */
  onAdd: (raw: string) => boolean;
  onRemove: (chip: string) => void;
  placeholder?: string;
  addLabel?: string;
  /** Visual style of the chips. */
  variant?: "indigo" | "amber";
}

/** A text input that turns submitted values into removable chips. */
export const ChipsField = ({
  chips,
  onAdd,
  onRemove,
  placeholder,
  addLabel = "Add",
  variant = "indigo",
}: ChipsFieldProps) => {
  const [value, setValue] = useState("");

  const commit = () => {
    if (onAdd(value)) setValue("");
  };

  const chipClass =
    variant === "amber"
      ? "border-amber-300 bg-amber-50 text-amber-700"
      : "border-indigo-200 bg-indigo-50 text-indigo-700";

  return (
    <div>
      {chips.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {chips.map((chip) => (
            <span
              key={chip}
              className={cn(
                "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium",
                chipClass,
              )}
            >
              {chip}
              <button
                type="button"
                onClick={() => onRemove(chip)}
                className="text-slate-400 hover:text-rose-600"
                aria-label={`Remove ${chip}`}
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Input
          size="sm"
          fullWidth
          value={value}
          onChange={setValue}
          placeholder={placeholder}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            }
          }}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={commit}
          disabled={!value.trim()}
        >
          {addLabel}
        </Button>
      </div>
    </div>
  );
};
