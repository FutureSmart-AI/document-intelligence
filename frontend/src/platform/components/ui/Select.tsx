import React, { forwardRef, memo, useCallback, useMemo } from "react";

import { map } from "lodash-es";
import { ChevronDown } from "lucide-react";

import Label from "./Label";

export type SelectSize = "xs" | "sm" | "md" | "lg";

export type SelectOption<T = string> =
  | string
  | {
      label: string;
      value: T;
      disabled?: boolean;
    };

type INormalizeOption = {
  label: string;
  value: string;
  disabled?: boolean;
};

export interface SelectProps<T = string> extends Omit<
  React.SelectHTMLAttributes<HTMLSelectElement>,
  "size" | "value" | "onChange"
> {
  size?: SelectSize;
  error?: boolean;
  errorMessage?: string;
  options: readonly SelectOption<T>[] | SelectOption<T>[];
  value?: T;
  onChange?: (value: T) => void;
  placeholder?: string;
  disabledPlaceholder?: boolean;
  fullWidth?: boolean;
  className?: string;
  label?: string | React.ReactNode;
}

const sizeClasses: Record<SelectSize, string> = {
  xs: "px-2 py-1 text-xs",
  sm: "px-2.5 py-1.5 text-sm",
  md: "px-3 py-2 text-sm",
  lg: "px-4 py-2.5 text-base",
};

const baseClasses =
  "appearance-none rounded-md border cursor-pointer bg-white shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:opacity-60 disabled:cursor-not-allowed";

const Select = forwardRef<HTMLSelectElement, SelectProps>(
  (
    {
      size = "md",
      error = false,
      errorMessage,
      options,
      value,
      onChange,
      placeholder,
      disabledPlaceholder = true,
      fullWidth = true,
      className = "",
      disabled,
      label,
      required,
      ...props
    },
    ref,
  ) => {
    const handleChange = useCallback(
      (e: React.ChangeEvent<HTMLSelectElement>) => {
        onChange?.(e.target.value as string);
      },
      [onChange],
    );

    const selectClasses = useMemo(() => {
      const borderClass = error
        ? "border-rose-500 focus:border-rose-500"
        : "border-slate-300 focus:border-indigo-500";

      const widthClass = fullWidth ? "w-full" : "";

      // Add padding-right to make room for the arrow icon
      const paddingRightClass = "pr-8";

      return `${baseClasses} ${sizeClasses[size]} ${borderClass} ${widthClass} ${paddingRightClass} ${className}`.trim();
    }, [error, fullWidth, size, className]);

    const normalizeOption = useCallback(
      (option: SelectOption): INormalizeOption => {
        if (typeof option === "string") {
          return { label: option, value: option };
        }
        return {
          label: option.label,
          value: String(option.value),
          disabled: option.disabled,
        };
      },
      [],
    );

    return (
      <div className={className}>
        {label && (
          <Label required={!!required} htmlFor={props.id || ""}>
            {label}
          </Label>
        )}
        <div className={fullWidth ? "w-full" : ""}>
          <div className="relative group">
            <select
              ref={ref}
              value={value ?? ""}
              onChange={handleChange}
              disabled={disabled}
              className={selectClasses}
              {...props}
            >
              {placeholder && (
                <option value="" disabled={disabledPlaceholder}>
                  {placeholder}
                </option>
              )}
              {useMemo(
                () =>
                  map(options, (option, index) => {
                    const normalized = normalizeOption(option);
                    return (
                      <option
                        key={normalized.value || index}
                        value={normalized.value}
                        disabled={normalized.disabled}
                      >
                        {normalized.label}
                      </option>
                    );
                  }),
                [options, normalizeOption],
              )}
            </select>
            <div
              className={`pointer-events-none absolute right-0 top-0 flex h-full items-center px-2.5 ${
                disabled ? "opacity-40" : ""
              }`}
            >
              <ChevronDown
                className={`size-4 transition-colors duration-200 ${
                  error ? "text-rose-500" : "text-slate-400"
                } group-focus-within:text-indigo-600`}
                strokeWidth={2.5}
                aria-hidden="true"
              />
            </div>
          </div>
          {error && errorMessage && (
            <p className="mt-1 text-xs text-rose-600">{errorMessage}</p>
          )}
        </div>
      </div>
    );
  },
);

Select.displayName = "Select";

/**
 * Custom comparison function for React.memo
 * Prevents re-renders when props haven't meaningfully changed
 */
const areEqual = <T,>(
  prevProps: SelectProps<T>,
  nextProps: SelectProps<T>,
): boolean => {
  // Compare primitive props
  if (
    prevProps.size !== nextProps.size ||
    prevProps.error !== nextProps.error ||
    prevProps.errorMessage !== nextProps.errorMessage ||
    prevProps.value !== nextProps.value ||
    prevProps.placeholder !== nextProps.placeholder ||
    prevProps.fullWidth !== nextProps.fullWidth ||
    prevProps.className !== nextProps.className ||
    prevProps.disabled !== nextProps.disabled ||
    prevProps.required !== nextProps.required ||
    prevProps.label !== nextProps.label
  ) {
    return false;
  }

  // Compare options array - check length and reference
  if (prevProps.options !== nextProps.options) {
    if (prevProps.options.length !== nextProps.options.length) {
      return false;
    }
    // If same length, do shallow comparison of each option
    for (let i = 0; i < prevProps.options.length; i++) {
      const prev = prevProps.options[i];
      const next = nextProps.options[i];
      if (prev !== next) {
        // If different references, check if they're actually different
        if (typeof prev === "string" || typeof next === "string") {
          if (prev !== next) return false;
        } else {
          if (
            prev.label !== next.label ||
            prev.value !== next.value ||
            prev.disabled !== next.disabled
          ) {
            return false;
          }
        }
      }
    }
  }

  // Compare onChange callback (should be stable via useCallback)
  if (prevProps.onChange !== nextProps.onChange) {
    return false;
  }

  // Compare other HTML attributes that might affect rendering
  const htmlAttrsToCompare: (keyof React.SelectHTMLAttributes<HTMLSelectElement>)[] =
    ["id", "name", "autoFocus", "form", "multiple"];

  for (const attr of htmlAttrsToCompare) {
    if (prevProps[attr] !== nextProps[attr]) {
      return false;
    }
  }

  return true;
};

export default memo(Select, areEqual);
