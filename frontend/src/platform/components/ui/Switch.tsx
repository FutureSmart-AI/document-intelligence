import React, { memo, useCallback } from "react";

import { cn } from "@/platform/utils";

import Label from "./Label";

export interface SwitchProps extends Omit<
  React.ButtonHTMLAttributes<HTMLButtonElement>,
  "onChange"
> {
  label?: string;
  id?: string;
  checked?: boolean;
  defaultChecked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  disabled?: boolean;
  size?: "sm" | "md" | "lg";
  error?: boolean;
  errorMessage?: string;
  required?: boolean;
}

const SIZES = {
  sm: {
    track: "h-5 w-9",
    thumb: "h-4 w-4",
    translate: {
      on: "translate-x-3.5",
      off: "translate-x-0.5",
    },
    disabled: "opacity-60",
  },
  md: {
    track: "h-6 w-11",
    thumb: "h-5 w-5",
    translate: {
      on: "translate-x-5",
      off: "translate-x-0.5",
    },
    disabled: "opacity-60",
  },
  lg: {
    track: "h-7 w-14",
    thumb: "h-6 w-6",
    translate: {
      on: "translate-x-7",
      off: "translate-x-0.5",
    },
    disabled: "opacity-60",
  },
};

export const Switch = ({
  label,
  id,
  checked,
  defaultChecked,
  onCheckedChange,
  disabled,
  size = "md",
  error,
  errorMessage,
  className,
  required,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledby,
}: SwitchProps) => {
  const currentChecked = Boolean(checked) || Boolean(defaultChecked);

  const handleToggle = useCallback(() => {
    if (disabled) return;
    const next = !currentChecked;
    onCheckedChange?.(next);
  }, [currentChecked, disabled, onCheckedChange]);

  const sizeClass = SIZES[size];

  const checkedClass = currentChecked ? "bg-indigo-600" : "bg-slate-300";
  const disabledClass = disabled ? sizeClass.disabled : "";
  const checkedTranslate = currentChecked
    ? sizeClass.translate.on
    : sizeClass.translate.off;

  return (
    <div className={className}>
      {label && (
        <Label required={required} htmlFor={id}>
          {label}
        </Label>
      )}
      <button
        type="button"
        role="switch"
        aria-checked={!!currentChecked}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledby}
        disabled={disabled}
        onClick={handleToggle}
        className={`relative cursor-pointer inline-flex ${sizeClass.track} items-center rounded-full transition-colors ${cn(
          checkedClass,
          disabledClass,
        )}`}
      >
        <span
          className={`inline-block  ${sizeClass.thumb} transform rounded-full bg-white shadow transition-transform ${cn(
            checkedTranslate,
            disabledClass,
          )}`}
        />
      </button>
      {error && errorMessage && (
        <p className="mt-1 text-xs text-rose-600">{errorMessage}</p>
      )}
    </div>
  );
};

Switch.displayName = "Switch";

export default memo(Switch);
