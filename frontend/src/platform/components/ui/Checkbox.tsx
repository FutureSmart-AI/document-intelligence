import React, { forwardRef, memo } from "react";

export type CheckboxSize = "sm" | "md" | "lg";

export interface CheckboxProps extends Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "size" | "type" | "onChange"
> {
  size?: CheckboxSize;
  checked?: boolean;
  defaultChecked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  label?: string | React.ReactNode;
  error?: boolean;
  errorMessage?: string;
  className?: string;
}

const sizeClasses: Record<CheckboxSize, string> = {
  sm: "h-3 w-3",
  md: "h-4 w-4",
  lg: "h-5 w-5",
};

const baseClasses =
  "rounded border-slate-300 text-indigo-600 focus:ring-indigo-600 focus:ring-2 focus:ring-offset-0 transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed";

const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  (
    {
      size = "md",
      checked,
      defaultChecked,
      onCheckedChange,
      label,
      error = false,
      errorMessage,
      disabled,
      className = "",
      id,
      ...props
    },
    ref,
  ) => {
    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      if (onCheckedChange) {
        onCheckedChange(e.target.checked);
      }
    };

    const borderClass = error
      ? "border-rose-500 focus:border-rose-500 focus:ring-rose-500"
      : "border-slate-300";

    const checkboxClasses =
      `${baseClasses} ${sizeClasses[size]} ${borderClass} ${className}`.trim();

    const checkboxId =
      id ||
      (label
        ? `checkbox-${Math.random().toString(36).substr(2, 9)}`
        : undefined);

    const checkbox = (
      <input
        ref={ref}
        type="checkbox"
        id={checkboxId}
        checked={checked}
        defaultChecked={defaultChecked}
        onChange={handleChange}
        disabled={disabled}
        className={checkboxClasses}
        {...props}
      />
    );

    if (!label && !errorMessage) {
      return checkbox;
    }

    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          {checkbox}
          {label && (
            <label
              htmlFor={checkboxId}
              className={`text-sm text-slate-700 cursor-pointer ${
                disabled ? "opacity-60 cursor-not-allowed" : ""
              }`}
            >
              {label}
            </label>
          )}
        </div>
        {error && errorMessage && (
          <p className="text-xs text-rose-600">{errorMessage}</p>
        )}
      </div>
    );
  },
);

Checkbox.displayName = "Checkbox";

export default memo(Checkbox);
