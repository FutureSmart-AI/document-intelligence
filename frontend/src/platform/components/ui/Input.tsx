import React, { forwardRef, memo } from "react";

import { Label } from ".";

export type InputSize = "xs" | "sm" | "md" | "lg";

// Use intersection of input and textarea attributes, excluding conflicting ones
type InputTextareaProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement> &
    React.TextareaHTMLAttributes<HTMLTextAreaElement>,
  "size" | "value" | "onChange" | "rows"
>;

export interface InputProps extends InputTextareaProps {
  size?: InputSize;
  error?: boolean;
  errorMessage?: string;
  value?: string;
  onChange?: (value: string) => void;
  fullWidth?: boolean;
  className?: string;
  as?: "input" | "textarea";
  rows?: number;
  label?: string | React.ReactNode;
}

const sizeClasses: Record<InputSize, string> = {
  xs: "px-2 py-1 text-xs",
  sm: "px-2.5 py-1.5 text-sm",
  md: "px-3 py-2 text-sm",
  lg: "px-4 py-2.5 text-base",
};

const colorSizeClasses: Record<InputSize, string> = {
  xs: "h-6 w-6",
  sm: "h-8 w-8",
  md: "h-10 w-24",
  lg: "h-12 w-32",
};

const baseClasses =
  "rounded-md border bg-white shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:opacity-60 disabled:cursor-not-allowed";

const Input = forwardRef<HTMLInputElement | HTMLTextAreaElement, InputProps>(
  (
    {
      size = "md",
      error = false,
      errorMessage,
      value,
      onChange,
      fullWidth = true,
      className = "",
      disabled,
      as = "input",
      rows,
      label,
      required,
      ...props
    },
    ref,
  ) => {
    const handleChange = (
      e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
    ) => {
      if (onChange) {
        onChange(e.target.value);
      }
    };

    const borderClass = error
      ? "border-rose-500 focus:border-rose-500"
      : "border-slate-300 focus:border-indigo-500";

    const isColorInput = props.type === "color";
    const widthClass = fullWidth && !isColorInput ? "w-full" : "";

    // Extract flex-1 from className if present
    const hasFlex1 = className.includes("flex-1");
    const inputClassName = className.replace("flex-1", "").trim();
    const wrapperClasses =
      fullWidth && !isColorInput ? "w-full" : hasFlex1 ? "flex-1" : "";

    // For color inputs, use special sizing and remove text padding
    const colorClasses = isColorInput
      ? `${colorSizeClasses[size]} px-1 py-1 cursor-pointer`
      : `${sizeClasses[size]}`;

    if (as === "textarea") {
      // Extract input-specific props that don't apply to textarea
      const { type: _type, ...textareaProps } = props;
      // Type assertion is safe here since we've removed input-specific props
      const textareaAttributes = textareaProps as Omit<
        React.TextareaHTMLAttributes<HTMLTextAreaElement>,
        "value" | "onChange" | "rows" | "disabled"
      >;

      return (
        <>
          {label && (
            <Label required={required} htmlFor={props.id}>
              {label}
            </Label>
          )}
          <div className={wrapperClasses}>
            <textarea
              ref={ref as React.ForwardedRef<HTMLTextAreaElement>}
              value={value ?? ""}
              onChange={handleChange}
              disabled={disabled}
              rows={rows}
              className={`${baseClasses} ${sizeClasses[size]} ${borderClass} ${hasFlex1 ? "w-full" : widthClass} ${inputClassName}`.trim()}
              {...textareaAttributes}
            />
            {error && errorMessage && (
              <p className="mt-1 text-xs text-rose-600">{errorMessage}</p>
            )}
          </div>
        </>
      );
    }

    return (
      <div className={className}>
        {label && (
          <Label required={required} htmlFor={props.id}>
            {label}
          </Label>
        )}
        <div className={wrapperClasses}>
          <input
            ref={ref as React.ForwardedRef<HTMLInputElement>}
            value={value ?? ""}
            onChange={handleChange}
            disabled={disabled}
            className={`${baseClasses} ${colorClasses} ${borderClass} ${hasFlex1 && !isColorInput ? "w-full" : widthClass} ${inputClassName}`.trim()}
            {...props}
          />
          {error && errorMessage && (
            <p className="mt-1 text-xs text-rose-600">{errorMessage}</p>
          )}
        </div>
      </div>
    );
  },
);

Input.displayName = "Input";

export default memo(Input);
